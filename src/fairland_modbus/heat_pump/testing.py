"""Register images of an MWH216 or MWH298 board, for tests without hardware.

Load one into ``modbus_connection.mock`` with ``load_image``. Addresses are the
protocol (PDU) addresses the Fairland register table prints.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Final

# A heat pump heating a pool at 26 degrees towards 28, in smart mode.
HEATING: Final[dict[str, dict[int, Any]]] = {
    "coil": {
        0: True,  # switched on
    },
    "discrete": {
        0: True,  # on
        3: True,  # DIN2: the customer control switch is closed
        4: True,  # DIN3: the water flow switch is closed
        7: True,  # OUT1
        17: True,  # the compressor is asked to run
    },
    "input": {
        0: 45,  # compressor at 45 %
        1: 52,  # target frequency 52 Hz
        2: 380,  # PFC 380 V
        3: 112,  # inlet water 26.0 degrees (type 1)
        4: 116,  # outlet water 28.0 degrees
        5: 101,  # ambient 20.5 degrees
        6: 140,  # exhaust gas 70.0 degrees (type 2)
        7: 84,  # evaporator coil 12.0 degrees
        8: 92,  # suction gas 16.0 degrees
        9: 118,  # heat exchanger coil 29.0 degrees
        10: 48,  # compressor 48 Hz
        11: 42,  # 4.2 A
        12: 90,  # cooling plate 45.0 degrees (type 2)
        13: 320,  # expansion valve at 320 steps
        14: 650,  # fan at 650 rpm
    },
    "holding": {
        0: 1,  # heating
        1: 0,  # smart
        2: 112,  # auto setpoint 26 degrees
        3: 116,  # heating setpoint 28 degrees
        4: 112,  # cooling setpoint 26 degrees
        5: 0,  # pump runs continuously (P0)
        6: 60,  # P1: 60 min
        7: 30,  # P2: 30 min
        8: 46,  # P3: -7 degrees
        9: 12,  # P4: 12 min
        10: 86,  # P5: 13 degrees
    },
}

# The only block reads the register table allows, as (start, count) per space:
# 48 bits from an allowed start for bits, 3 registers for registers.
ALLOWED_READS: Final[dict[str, frozenset[tuple[int, int]]]] = {
    "coil": frozenset({(0, 48)}),
    "discrete": frozenset({(0, 48), (48, 48)}),
    "input": frozenset({(start, 3) for start in range(0, 15, 3)}),
    "holding": frozenset({(start, 3) for start in range(0, 21, 3)}),
}

# The addresses the library may write. Coil 2 restores the factory settings
# and must never appear here.
WRITABLE: Final[dict[str, frozenset[int]]] = {
    "coil": frozenset({0, 1}),
    "holding": frozenset(range(0, 11)),
}


def load_image(unit: Any, image: dict[str, dict[int, Any]] = HEATING) -> None:
    """Seed a ``MockModbusUnit`` with a copy of a register image."""
    for space, values in deepcopy(image).items():
        getattr(unit, space).update(values)
