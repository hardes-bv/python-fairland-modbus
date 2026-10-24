"""End-to-end tests against tools/simulator.py, over real Modbus TCP and RTU.

The simulator is written from the register table alone and refuses any read
that breaks the table's block rules, so these tests catch an address or block
mistake that the mock would let through.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from types import ModuleType
from typing import Any, Final, Literal

import pytest
from modbus_connection import (
    IllegalDataAddressError,
    ModbusSerialParams,
    ModbusTcpParams,
)
from modbus_connection.model import Component, integer
from modbus_connection.tmodbus import ModbusConnection

from fairland_modbus import (
    FairlandRefusedError,
    FairlandTimeoutError,
    NotAFairlandDeviceError,
)
from fairland_modbus.heat_pump import (
    FairlandHeatPump,
    Function,
    WorkingMode,
)

_SIMULATOR = Path(__file__).resolve().parent.parent / "tools" / "simulator.py"

# The board's fixed line settings: 9600 baud, 8 data bits, no parity, 1 stop bit.
_BAUDRATE: Final = 9600
_PARITY: Final[Literal["N"]] = "N"
_STOPBITS: Final[Literal[1]] = 1


def _load_simulator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("simulator", _SIMULATOR)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["simulator"] = module
    spec.loader.exec_module(module)
    return module


simulator = _load_simulator()


async def _tcp(**options: Any) -> AsyncIterator[tuple[Any, ModbusConnection]]:
    board = simulator.HeatPump(**options.pop("board", {}))
    server = await simulator.serve_tcp(board, 0, 1, **options)
    connection = ModbusConnection(
        ModbusTcpParams(host="127.0.0.1", port=simulator.server_port(server)),
        timeout=2,
    )
    try:
        yield board, connection
    finally:
        await connection.close()
        server.close()
        await server.wait_closed()


@pytest.fixture
async def tcp() -> AsyncIterator[tuple[Any, ModbusConnection]]:
    """A simulated heat pump behind a Modbus TCP gateway."""
    async for pair in _tcp():
        yield pair


async def test_polls_the_simulator_over_tcp(
    tcp: tuple[Any, ModbusConnection],
) -> None:
    """Every block the library reads is one the board accepts."""
    board, connection = tcp
    heat_pump = FairlandHeatPump(connection.for_unit(1))

    await heat_pump.async_validate()
    report = await heat_pump.async_update()

    assert report.complete
    assert heat_pump.readings.inlet_water_temperature == 26.0
    assert heat_pump.settings.heating_target_temperature == 28.0
    assert heat_pump.status.compressor_demand is True
    assert board.requests["exception 02"] == 0


async def test_commands_change_the_simulated_board(
    tcp: tuple[Any, ModbusConnection],
) -> None:
    """Power, function, mode and setpoint writes land where the table says."""
    board, connection = tcp
    heat_pump = FairlandHeatPump(connection.for_unit(1))
    await heat_pump.async_update()

    await heat_pump.async_set_power(False)
    await heat_pump.async_set_function(Function.COOLING)
    await heat_pump.async_set_working_mode(WorkingMode.SUPER_SILENCE)
    await heat_pump.async_set_target_temperature(25)
    await heat_pump.async_set_setting("defrost_start_temperature", -10)
    await heat_pump.async_update()

    assert heat_pump.controls.power is False
    assert heat_pump.status.running is False
    assert heat_pump.settings.function is Function.COOLING
    assert heat_pump.settings.working_mode is WorkingMode.SUPER_SILENCE
    assert board.holding[4] == 110
    assert board.holding[8] == 40
    assert heat_pump.target_temperature == 25.0


async def test_a_forced_defrost_shows_up_in_the_status(
    tcp: tuple[Any, ModbusConnection],
) -> None:
    """Coil 1 starts a defrost, which discrete input 1 reports."""
    _, connection = tcp
    heat_pump = FairlandHeatPump(connection.for_unit(1))

    await heat_pump.async_start_defrost()
    await heat_pump.async_update()

    assert heat_pump.status.defrosting is True
    assert heat_pump.readings.compressor_speed == 0


async def test_reports_no_water_flow() -> None:
    """An E3 board stops its compressor and raises the malfunction flag."""
    async for _, connection in _tcp(board={"faults": ("E3",)}):
        heat_pump = FairlandHeatPump(connection.for_unit(1))
        await heat_pump.async_update()

        assert heat_pump.status.no_water_flow is True
        assert heat_pump.status.flow_switch_closed is False
        assert heat_pump.status.malfunction is True
        assert heat_pump.status.active_codes == ("E3",)
        assert heat_pump.status.compressor_demand is False


async def test_a_board_without_the_mode_refuses_it() -> None:
    """A board that refuses holding 1 surfaces FairlandRefusedError."""
    async for _, connection in _tcp(board={"refuse_working_mode": True}):
        heat_pump = FairlandHeatPump(connection.for_unit(1))

        with pytest.raises(FairlandRefusedError):
            await heat_pump.async_set_working_mode(WorkingMode.SILENCE)


async def test_a_board_keeping_its_mode_is_caught() -> None:
    """A board that acknowledges turbo and keeps its mode surfaces as refused."""
    async for _, connection in _tcp(board={"keep_working_mode": True}):
        heat_pump = FairlandHeatPump(connection.for_unit(1))
        heat_pump.working_mode_settle = 0

        await heat_pump.async_set_working_mode(WorkingMode.SILENCE)
        with pytest.raises(FairlandRefusedError, match="kept the working mode silence"):
            await heat_pump.async_set_working_mode(WorkingMode.TURBO)


async def test_a_silent_board_behind_the_gateway_times_out() -> None:
    """A gateway answering 0B on every request means the pump is off."""
    async for _, connection in _tcp(gateway_silent=True):
        heat_pump = FairlandHeatPump(connection.for_unit(1))

        with pytest.raises(FairlandTimeoutError):
            await heat_pump.async_update()
        with pytest.raises(FairlandTimeoutError):
            await heat_pump.async_validate()


async def test_a_board_of_another_family_is_not_this_board() -> None:
    """A board refusing every function of the table fails validation."""
    async for _, connection in _tcp(other_board=True):
        heat_pump = FairlandHeatPump(connection.for_unit(1))

        with pytest.raises(NotAFairlandDeviceError):
            await heat_pump.async_validate()


async def test_the_simulator_refuses_a_read_outside_the_blocks(
    tcp: tuple[Any, ModbusConnection],
) -> None:
    """A single-register read, as older projects do, breaks the table rules."""

    class SingleRegister(Component):
        register_space = "input"
        inlet = integer(3)

    _, connection = tcp

    with pytest.raises(IllegalDataAddressError):
        await SingleRegister(connection.for_unit(1)).async_update()


async def test_polls_the_simulator_over_rtu() -> None:
    """The same poll works over a serial line at 9600 baud, 8N1."""
    board = simulator.HeatPump()
    loop = asyncio.get_running_loop()
    ready: asyncio.Future[str] = loop.create_future()
    task = asyncio.create_task(simulator.serve_rtu(board, 1, ready))
    port = await ready
    connection = ModbusConnection(
        ModbusSerialParams(
            device=port,
            baudrate=_BAUDRATE,
            parity=_PARITY,
            stopbits=_STOPBITS,
        ),
        timeout=2,
    )
    try:
        heat_pump = FairlandHeatPump(connection.for_unit(1))
        report = await heat_pump.async_update()
        await heat_pump.async_set_target_temperature(30)
        await heat_pump.async_update()
    finally:
        await connection.close()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert report.complete
    assert heat_pump.target_temperature == 30.0
    assert board.holding[3] == 120
