"""Enums and limits of the MWH216 and MWH298 heat pump boards.

Everything here comes from the Fairland register table for the PC boards
MWH216 and MWH298, unless a comment says otherwise.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum
from typing import Final

# The unit ID the board answers at.
DEFAULT_UNIT_ID: Final = 1

# The table asks for at least 60 ms between two requests.
MESSAGE_SPACING: Final = 0.06


class Function(IntEnum):
    """What the heat pump does to the water (holding register 0)."""

    AUTO = 0
    HEATING = 1
    COOLING = 2


class WorkingMode(IntEnum):
    """How hard the compressor may run (holding register 1).

    The table documents 0, 1 and 3. Value 2 is the "super silence" mode that
    owners of IPHCR heat pumps found by trial; the table leaves it out, and
    some boards refuse it. Turbo is missing on some models.
    """

    SMART = 0
    SILENCE = 1
    SUPER_SILENCE = 2
    TURBO = 3


class PumpMode(IntEnum):
    """When the board runs the filtration pump (holding register 5, P0)."""

    CONTINUOUS = 0
    WATER_TEMPERATURE = 1
    TIME_AND_WATER_TEMPERATURE = 2


class FaultKind(StrEnum):
    """Whether a code is a protection that clears by itself, or a failure."""

    PROTECTION = "protection"
    FAILURE = "failure"


# The fault and protection codes the board reports, by discrete input address.
# The table names them only. The meanings, and which ones are mere
# protections, come from the Fairland InverX installation manual.
FAULT_CODES: Final[dict[int, str]] = {
    **{48 + index: f"E{char}" for index, char in enumerate("0123456789Ab")},
    61: "Ed",
    **{64 + index: f"P{char}" for index, char in enumerate("0123456789A")},
    **{80 + index: f"F{char}" for index, char in enumerate("0123456789Ab")},
}

# Codes the manual lists as "not failure": the unit protects itself and
# resumes once the condition is gone.
PROTECTION_CODES: Final = frozenset({"E3", "E5", "E6", "Eb", "Ed"})

# The no-water-flow protection (E3), the most common code in practice.
NO_WATER_FLOW_CODE: Final = "E3"


def fault_kind(code: str) -> FaultKind:
    """Whether ``code`` is a protection or a failure."""
    return FaultKind.PROTECTION if code in PROTECTION_CODES else FaultKind.FAILURE


# Setpoint ranges in whole degrees Celsius, per function (holding 2 to 4).
# The table gives them as raw values with a step of 2, that is one degree.
TARGET_TEMPERATURE_LIMITS: Final[dict[Function, tuple[int, int]]] = {
    Function.AUTO: (12, 40),  # raw 84 to 140
    Function.HEATING: (18, 40),  # raw 96 to 140
    Function.COOLING: (12, 30),  # raw 84 to 120
}
