"""A simulator of a heat pump with an MWH216 or MWH298 board.

It serves Modbus TCP, or Modbus RTU on a pseudo-terminal, and behaves the way
the Fairland register table describes. It is written from the table alone and
does not import fairland_modbus, so an address mistake in the library shows up
here as a refused or wrong read.

It is strict like the board's own rules: a read must start at an address the
table allows and cover exactly 48 bits or 3 registers. Anything else is
answered with exception 02 (illegal data address), where the real board would
return wrong data without warning.

Usage:
    python tools/simulator.py --tcp 5020
    python tools/simulator.py --rtu                  # prints the serial port
    python tools/simulator.py --tcp 5020 --fault E3  # no water flow
    python tools/simulator.py --tcp 5020 --refuse-working-mode
    python tools/simulator.py --tcp 5020 --keep-working-mode  # like a GEPAC08
    python tools/simulator.py --tcp 5020 --gateway-silent
    python tools/simulator.py --tcp 5020 --other-board  # like a PC1004

Behaviour taken from the table:
    coil 0             power; discrete input 0 follows it
    coil 1 = 1         forced defrost: discrete input 1 is on for a while
    coil 2 = 1         factory reset: logged as DANGEROUS, then ignored
    holding 0          function: 0 auto, 1 heating, 2 cooling
    holding 1          working mode: 0 smart, 1 silence, 3 turbo, and the
                       undocumented 2 (super silence) that IPHCR owners use
    holding 2 to 10    setpoints and service parameters, range checked

Behaviour seen on a real Geco GEPAC08:
    --fault E3         also opens DIN3 (discrete input 4), the water flow switch
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import struct
import termios
import time
import tty
from collections import Counter
from dataclasses import dataclass, field

_LOGGER = logging.getLogger("fairland_heat_pump_simulator")

# The working modes a GEPAC08 acknowledges without switching to them.
_KEPT_MODES = frozenset({2, 3})

# Where a read may start, and how much it must read, per function code.
BIT_BLOCK = 48
REGISTER_BLOCK = 3
READ_STARTS = {
    1: {0},  # coils
    2: {0, 48},  # discrete inputs
    3: {0, 3, 6, 9, 12, 15, 18},  # holding registers
    4: {0, 3, 6, 9, 12},  # input registers
}
READ_COUNTS = {1: BIT_BLOCK, 2: BIT_BLOCK, 3: REGISTER_BLOCK, 4: REGISTER_BLOCK}

# Writable holding registers and the raw ranges and steps the table gives.
WRITABLE = {
    0: (0, 2, 1),  # function
    1: (0, 3, 1),  # working mode
    2: (84, 140, 2),  # auto setpoint
    3: (96, 140, 2),  # heating setpoint
    4: (84, 120, 2),  # cooling setpoint
    5: (0, 2, 1),  # P0 pump mode
    6: (10, 120, 5),  # P1 pump run time
    7: (30, 90, 1),  # P2 compressor run time before defrost
    8: (26, 60, 2),  # P3 defrost start temperature
    9: (1, 12, 1),  # P4 longest defrost
    10: (76, 120, 2),  # P5 defrost end temperature
    17: (40, 100, 2),  # EEV overheat level
    18: (40, 100, 2),  # EEV overheat level
    25: (0, 1, 1),  # power-off restart memory
}

# The code names, by discrete input, as the table lists them.
CODES = {
    **{48 + i: f"E{c}" for i, c in enumerate("0123456789Ab")},
    61: "Ed",
    **{64 + i: f"P{c}" for i, c in enumerate("0123456789A")},
    **{80 + i: f"F{c}" for i, c in enumerate("0123456789Ab")},
}
DEFROST_SECONDS = 20


def type_1(degrees: float) -> int:
    """A type 1 temperature as the board sends it."""
    return round((degrees + 30) * 2)


@dataclass
class HeatPump:
    """The board's state, by table address."""

    faults: tuple[str, ...] = ()
    refuse_working_mode: bool = False
    keep_working_mode: bool = False
    coils: dict[int, bool] = field(default_factory=dict)
    discrete: dict[int, bool] = field(default_factory=dict)
    inputs: dict[int, int] = field(default_factory=dict)
    holding: dict[int, int] = field(default_factory=dict)
    requests: Counter[str] = field(default_factory=Counter)
    defrost_until: float = 0.0

    def __post_init__(self) -> None:
        """Load the defaults the table gives, on a pool at 26 degrees."""
        self.coils = dict.fromkeys(range(48), False)
        self.coils[0] = True
        self.discrete = dict.fromkeys(range(96), False)
        self.discrete[3] = True  # DIN2: customer control switch closed
        for address, code in CODES.items():
            self.discrete[address] = code in self.faults
        self.inputs = dict.fromkeys(range(15), 0)
        self.inputs.update(
            {
                2: 380,
                3: type_1(26),
                4: type_1(28),
                5: type_1(20.5),
                6: 140,  # type 2: 70 degrees
                7: type_1(12),
                8: type_1(16),
                9: type_1(29),
                11: 42,
                12: 90,  # type 2: 45 degrees
                13: 320,
            }
        )
        self.holding = dict.fromkeys(range(33), 0)
        self.holding.update(
            {
                0: 1,
                1: 0,
                2: 112,
                3: 116,
                4: 112,
                5: 0,
                6: 60,
                7: 30,
                8: 46,
                9: 12,
                10: 86,
                17: 66,
                18: 64,
                25: 1,
            }
        )
        self._update()

    def _update(self) -> None:
        """Derive the running state and the compressor from the commands."""
        on = self.coils[0]
        defrosting = on and time.monotonic() < self.defrost_until
        flow_ok = "E3" not in self.faults
        self.discrete[0] = on
        self.discrete[1] = defrosting
        self.discrete[4] = flow_ok  # DIN3: the water flow switch
        self.discrete[16] = any(self.discrete[a] for a in CODES)
        running = on and flow_ok and not defrosting
        self.discrete[17] = running
        self.discrete[7] = running  # OUT1: compressor
        speed = {0: 70, 1: 45, 2: 24, 3: 100}[self.holding[1]] if running else 0
        self.inputs[0] = speed
        self.inputs[1] = round(speed * 0.9)
        self.inputs[10] = round(speed * 0.85)
        self.inputs[11] = round(speed * 0.6)
        self.inputs[14] = 650 if running else 0

    # Modbus function handlers. Each returns the response PDU.

    def handle(self, pdu: bytes) -> bytes:
        """Answer one request PDU."""
        function = pdu[0]
        self.requests[f"FC{function:02d}"] += 1
        self._update()
        try:
            if function in (1, 2, 3, 4):
                start, count = struct.unpack(">HH", pdu[1:5])
                return self._read(function, start, count)
            if function == 5:
                address, value = struct.unpack(">HH", pdu[1:5])
                self._write_coil(address, value == 0xFF00)
                return pdu[:5]
            if function == 6:
                address, value = struct.unpack(">HH", pdu[1:5])
                self._write_register(address, value)
                return pdu[:5]
            # The table lists FC05 and FC06 only for writes.
            return bytes([function | 0x80, 1])
        except _ModbusException as err:
            self.requests[f"exception {err.code:02d}"] += 1
            return bytes([function | 0x80, err.code])

    def _read(self, function: int, start: int, count: int) -> bytes:
        """Answer a read, but only the way the table allows."""
        if start not in READ_STARTS[function] or count != READ_COUNTS[function]:
            _LOGGER.warning(
                "Refused FC%02d at %s for %s: the table needs a read of %s "
                "from one of %s",
                function,
                start,
                count,
                READ_COUNTS[function],
                sorted(READ_STARTS[function]),
            )
            raise _ModbusException(2)
        space = {1: self.coils, 2: self.discrete, 3: self.holding, 4: self.inputs}[
            function
        ]
        addresses = range(start, start + count)
        if function in (1, 2):
            bits = sum(int(space[a]) << i for i, a in enumerate(addresses))
            data = bits.to_bytes((count + 7) // 8, "little")
        else:
            data = b"".join(struct.pack(">H", space[a]) for a in addresses)
        return bytes([function, len(data)]) + data

    def _write_coil(self, address: int, value: bool) -> None:
        if address not in (0, 1, 2):
            raise _ModbusException(2)
        _LOGGER.info("Coil %s <- %s", address, int(value))
        if address == 2:
            if value:
                _LOGGER.error("DANGEROUS: factory reset requested (ignored)")
            return
        if address == 1:
            if value and self.coils[0]:
                self.defrost_until = time.monotonic() + DEFROST_SECONDS
                _LOGGER.info("Forced defrost for %s s", DEFROST_SECONDS)
            return  # a command: the coil keeps reading 0
        self.coils[0] = value
        self._update()

    def _write_register(self, address: int, value: int) -> None:
        if address not in WRITABLE:
            raise _ModbusException(2)
        if address == 1 and self.refuse_working_mode:
            _LOGGER.warning("Working mode %s refused with an exception", value)
            raise _ModbusException(3)
        if address == 1 and self.keep_working_mode and value in _KEPT_MODES:
            # A GEPAC08 owner saw this: the write is acknowledged and the
            # board keeps its mode.
            _LOGGER.warning("Working mode %s acknowledged and ignored", value)
            return
        low, high, step = WRITABLE[address]
        if not low <= value <= high or (value - low) % step:
            _LOGGER.warning("Holding %s <- %s refused: out of range", address, value)
            raise _ModbusException(3)
        _LOGGER.info("Holding %s <- %s", address, value)
        self.holding[address] = value
        self._update()


class _ModbusException(Exception):
    """A Modbus exception response."""

    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code


async def serve_tcp(
    unit: HeatPump,
    port: int,
    unit_id: int,
    *,
    gateway_silent: bool = False,
    other_board: bool = False,
) -> asyncio.Server:
    """Serve Modbus TCP on ``port``, like a gateway in "Modbus TCP to RTU" mode.

    With ``gateway_silent``, the gateway answers but the heat pump behind it
    does not: every request gets exception 0B (gateway target failed). With
    ``other_board``, a board of another family answers every request with
    exception 01 (illegal function), as a PC1004 does.
    """

    async def client(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            while True:
                header = await reader.readexactly(7)
                transaction, protocol, length, target = struct.unpack(">HHHB", header)
                pdu = await reader.readexactly(length - 1)
                if gateway_silent:
                    response = bytes([pdu[0] | 0x80, 0x0B])
                elif other_board:
                    response = bytes([pdu[0] | 0x80, 0x01])
                elif target != unit_id:
                    response = bytes([pdu[0] | 0x80, 0x0B])  # nobody at that ID
                else:
                    response = unit.handle(pdu)
                writer.write(
                    struct.pack(
                        ">HHHB", transaction, protocol, len(response) + 1, target
                    )
                    + response
                )
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(client, "127.0.0.1", port)
    _LOGGER.info("Modbus TCP on 127.0.0.1:%s, unit %s", server_port(server), unit_id)
    return server


def server_port(server: asyncio.Server) -> int:
    """The port a server listens on."""
    port: int = server.sockets[0].getsockname()[1]
    return port


def crc16(data: bytes) -> bytes:
    """The Modbus RTU CRC of a frame, low byte first."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return struct.pack("<H", crc)


def _request_length(frame: bytes) -> int | None:
    """The full length of an RTU request, once enough of it has arrived."""
    if len(frame) < 2:
        return None
    if frame[1] in (1, 2, 3, 4, 5, 6):
        return 8
    if frame[1] in (15, 16):
        return 9 + frame[6] if len(frame) >= 7 else None
    return 4


async def serve_rtu(
    unit: HeatPump, unit_id: int, ready: asyncio.Future[str] | None = None
) -> None:
    """Serve Modbus RTU on a new pseudo-terminal, and print its path."""
    controller_fd, terminal_fd = os.openpty()
    tty.setraw(controller_fd)
    attrs = termios.tcgetattr(terminal_fd)
    tty.setraw(terminal_fd)
    termios.tcsetattr(terminal_fd, termios.TCSANOW, attrs)
    path = os.ttyname(terminal_fd)
    _LOGGER.info("Modbus RTU on %s, unit %s", path, unit_id)
    print(path, flush=True)
    if ready is not None:
        ready.set_result(path)
    loop = asyncio.get_running_loop()
    buffer = bytearray()
    queue: asyncio.Queue[bytes] = asyncio.Queue()
    loop.add_reader(
        controller_fd, lambda: queue.put_nowait(os.read(controller_fd, 256))
    )
    try:
        while True:
            buffer += await queue.get()
            while (length := _request_length(bytes(buffer))) is not None and len(
                buffer
            ) >= length:
                frame, buffer = bytes(buffer[:length]), buffer[length:]
                if crc16(frame[:-2]) != frame[-2:]:
                    _LOGGER.warning("Bad CRC, frame dropped")
                    buffer.clear()
                    break
                if frame[0] != unit_id:
                    continue
                response = bytes([unit_id]) + unit.handle(frame[1:-2])
                os.write(controller_fd, response + crc16(response))
    finally:
        loop.remove_reader(controller_fd)
        os.close(controller_fd)
        os.close(terminal_fd)


async def main() -> None:
    """Run the simulator from the command line."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--tcp", type=int, help="serve Modbus TCP on this port")
    parser.add_argument(
        "--rtu", action="store_true", help="serve Modbus RTU on a pseudo-terminal"
    )
    parser.add_argument("--unit-id", type=int, default=1)
    parser.add_argument(
        "--fault", action="append", default=[], help="an active code, like E3"
    )
    parser.add_argument(
        "--refuse-working-mode",
        action="store_true",
        help="refuse every working mode write with exception 03",
    )
    parser.add_argument(
        "--keep-working-mode",
        action="store_true",
        help="acknowledge super silence and turbo but keep the mode, like a GEPAC08",
    )
    parser.add_argument(
        "--other-board",
        action="store_true",
        help="answer like a board of another family (exception 01)",
    )
    parser.add_argument(
        "--gateway-silent",
        action="store_true",
        help="answer like a gateway whose heat pump is off (exception 0B)",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    unit = HeatPump(
        faults=tuple(args.fault),
        refuse_working_mode=args.refuse_working_mode,
        keep_working_mode=args.keep_working_mode,
    )
    tasks = []
    if args.tcp:
        server = await serve_tcp(
            unit,
            args.tcp,
            args.unit_id,
            gateway_silent=args.gateway_silent,
            other_board=args.other_board,
        )
        tasks.append(server.serve_forever())
    if args.rtu:
        tasks.append(serve_rtu(unit, args.unit_id))
    if not tasks:
        parser.error("pass --tcp PORT and/or --rtu")
    try:
        await asyncio.gather(*tasks)
    finally:
        _LOGGER.info("Requests served: %s", dict(unit.requests))


if __name__ == "__main__":
    asyncio.run(main())
