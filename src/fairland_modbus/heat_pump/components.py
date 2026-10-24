"""Register layout of the MWH216 and MWH298 heat pump boards.

Addresses are protocol (PDU) addresses, the numbers the Fairland register
table prints. The table sets two read rules, and the layout below follows
both:

* a read must start at an address the table marks as allowed, and
* a read of coils or discrete inputs covers exactly 48 bits, and a read of
  input or holding registers exactly 3 registers.

Otherwise the board "may return incorrect data". Each component therefore
declares its readable ranges as those fixed blocks, and claims the first and
last address of every block so the planner reads each block whole. The
claimed placeholders are private and never decoded into anything useful.

Temperatures come in two encodings, which the table calls types:

* type 1: degrees = raw / 2 - 30, so raw 120 is 30 degrees;
* type 2: degrees = raw / 2.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from modbus_connection.model import (
    Component,
    NumberField,
    coil,
    discrete_input,
    enum,
    gauge,
    integer,
    raw_register,
)

from .const import (
    FAULT_CODES,
    NO_WATER_FLOW_CODE,
    TARGET_TEMPERATURE_LIMITS,
    Function,
    PumpMode,
    WorkingMode,
)

# The table numbers the DIN and OUT terminals from 1.
DIGITAL_INPUTS = range(1, 6)
DIGITAL_OUTPUTS = range(1, 10)


def _check_number(value: Any) -> float:
    """Return ``value`` as a number, or raise ``ValueError``."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"expected a number, got {value!r}")
    return value


def whole_number(low: int, high: int, step: int = 1) -> Callable[[Any], int]:
    """A validator for whole numbers from ``low`` to ``high``, in ``step``."""

    def _validate(value: Any) -> int:
        number = _check_number(value)
        if int(number) != number:
            raise ValueError(f"expected a whole number, got {value!r}")
        if not low <= number <= high or (int(number) - low) % step:
            raise ValueError(
                f"expected {low} to {high} in steps of {step}, got {value!r}"
            )
        return int(number)

    return _validate


def whole_degrees(low: int, high: int) -> Callable[[Any], float]:
    """A validator for a setpoint in whole degrees from ``low`` to ``high``.

    The board steps temperature settings by 2 raw units, which is one degree.
    """
    check = whole_number(low, high)

    def _validate(value: Any) -> float:
        return float(check(value))

    return _validate


def member[E: Function | WorkingMode | PumpMode](
    enum_type: type[E],
) -> Callable[[Any], E]:
    """A validator that accepts only members of ``enum_type``."""

    def _validate(value: Any) -> E:
        if isinstance(value, bool):
            raise ValueError(f"expected a {enum_type.__name__}, got {value!r}")
        try:
            return enum_type(value)
        except ValueError:
            raise ValueError(
                f"expected a {enum_type.__name__}, got {value!r}"
            ) from None

    return _validate


def _type_1(
    address: int, *, writable: bool | Callable[[Any], float] = False
) -> NumberField[float]:
    """A type 1 temperature: degrees = raw / 2 - 30."""
    return gauge(address, 0.5, offset=-30, signed=False, writable=writable, unit="°C")


def _type_2(address: int) -> NumberField[float]:
    """A type 2 temperature: degrees = raw / 2."""
    return gauge(address, 0.5, signed=False, unit="°C")


class Controls(Component):
    """The power switch and the forced defrost, from the coils (FC01 / FC05).

    Coil 2 restores the factory settings. It is deliberately not modelled.
    """

    coil_ranges = ((0, 47),)

    power = coil(0, writable=True)
    """Whether the heat pump is switched on."""

    _defrost_request = coil(1, writable=True)  # 0: nothing, 1: start a defrost
    _block_end = coil(47)  # reserved, claimed so the read covers all 48 bits

    async def async_set_power(self, on: bool) -> None:
        """Switch the heat pump on or off."""
        await self.write("power", bool(on))

    async def async_start_defrost(self) -> None:
        """Ask the board to defrost now."""
        await self.write("_defrost_request", True)


class Status(Component):
    """Running state, terminals, alarms and codes, from the discrete inputs.

    The table documents two blocks of 48 bits: 0 to 47 and 48 to 95.
    """

    discrete_ranges = ((0, 47), (48, 95))

    running = discrete_input(0)
    """Whether the heat pump is on."""

    defrosting = discrete_input(1)
    """Whether the heat pump is defrosting."""

    _din_1 = discrete_input(2)
    _din_2 = discrete_input(3)
    _din_3 = discrete_input(4)
    _din_4 = discrete_input(5)
    _din_5 = discrete_input(6)
    _out_1 = discrete_input(7)
    _out_2 = discrete_input(8)
    _out_3 = discrete_input(9)
    _out_4 = discrete_input(10)
    _out_5 = discrete_input(11)
    _out_6 = discrete_input(12)
    _out_7 = discrete_input(13)
    _out_8 = discrete_input(14)
    _out_9 = discrete_input(15)

    malfunction = discrete_input(16)
    """Whether any fault or protection is active."""

    compressor_demand = discrete_input(17)
    """Whether the board asks the compressor to run."""

    _block_1_end = discrete_input(47)  # reserved, claimed to read all 48 bits

    _code_e0 = discrete_input(48)
    _code_e1 = discrete_input(49)
    _code_e2 = discrete_input(50)
    _code_e3 = discrete_input(51)
    _code_e4 = discrete_input(52)
    _code_e5 = discrete_input(53)
    _code_e6 = discrete_input(54)
    _code_e7 = discrete_input(55)
    _code_e8 = discrete_input(56)
    _code_e9 = discrete_input(57)
    _code_ea = discrete_input(58)
    _code_eb = discrete_input(59)
    _code_ed = discrete_input(61)
    _code_p0 = discrete_input(64)
    _code_p1 = discrete_input(65)
    _code_p2 = discrete_input(66)
    _code_p3 = discrete_input(67)
    _code_p4 = discrete_input(68)
    _code_p5 = discrete_input(69)
    _code_p6 = discrete_input(70)
    _code_p7 = discrete_input(71)
    _code_p8 = discrete_input(72)
    _code_p9 = discrete_input(73)
    _code_pa = discrete_input(74)
    _code_f0 = discrete_input(80)
    _code_f1 = discrete_input(81)
    _code_f2 = discrete_input(82)
    _code_f3 = discrete_input(83)
    _code_f4 = discrete_input(84)
    _code_f5 = discrete_input(85)
    _code_f6 = discrete_input(86)
    _code_f7 = discrete_input(87)
    _code_f8 = discrete_input(88)
    _code_f9 = discrete_input(89)
    _code_fa = discrete_input(90)
    _code_fb = discrete_input(91)

    _block_2_end = discrete_input(95)  # reserved, claimed to read all 48 bits

    def digital_input(self, number: int) -> bool | None:
        """Whether terminal DIN ``number`` (1 to 5) is connected."""
        if number not in DIGITAL_INPUTS:
            raise ValueError(f"DIN{number} does not exist")
        value: bool | None = getattr(self, f"_din_{number}")
        return value

    def digital_output(self, number: int) -> bool | None:
        """Whether output OUT ``number`` (1 to 9) is driven."""
        if number not in DIGITAL_OUTPUTS:
            raise ValueError(f"OUT{number} does not exist")
        value: bool | None = getattr(self, f"_out_{number}")
        return value

    def code_active(self, code: str) -> bool | None:
        """Whether fault or protection ``code``, like ``"E3"``, is active."""
        if code not in FAULT_CODES.values():
            raise ValueError(f"unknown code {code!r}")
        value: bool | None = getattr(self, f"_code_{code.lower()}")
        return value

    @property
    def active_codes(self) -> tuple[str, ...]:
        """The active codes, in the order the table lists them."""
        return tuple(code for code in FAULT_CODES.values() if self.code_active(code))

    @property
    def remote_switch_closed(self) -> bool | None:
        """Whether the remote on/off contact on DIN2 is closed.

        The manual shows "OFF" while this contact is open, and owners wire a
        volt-free switch or a pool controller relay to it.
        """
        return self._din_2

    @property
    def flow_switch_closed(self) -> bool | None:
        """Whether the water flow switch on DIN3 is closed, so water flows.

        Unlike the E3 protection, it follows the water while the heat pump is
        off. Seen on a Geco GEPAC08 with an MWH298-V3 board.
        """
        return self._din_3

    @property
    def no_water_flow(self) -> bool | None:
        """Whether the no-water-flow protection (E3) is active."""
        return self.code_active(NO_WATER_FLOW_CODE)


class Readings(Component):
    """Compressor and temperature readings, from the input registers (FC04)."""

    register_space = "input"
    register_ranges = ((0, 2), (3, 5), (6, 8), (9, 11), (12, 14))

    compressor_speed = integer(0, signed=False, unit="%")
    """The compressor speed, as a percentage of its maximum."""

    compressor_target_frequency = integer(1, signed=False, unit="Hz")
    """The frequency the inverter drives the compressor towards."""

    pfc_voltage = integer(2, signed=False, unit="V")
    """The voltage of the power factor correction stage."""

    inlet_water_temperature = _type_1(3)
    """The water entering the heat pump (AIN1), the pool temperature."""

    outlet_water_temperature = _type_1(4)
    """The water leaving the heat pump (AIN2)."""

    ambient_temperature = _type_1(5)
    """The outdoor air (AIN7)."""

    exhaust_gas_temperature = _type_2(6)
    """The refrigerant leaving the compressor (AIN5)."""

    evaporator_coil_temperature = _type_1(7)
    """The outer coil pipe, the evaporator in heating (AIN3)."""

    suction_gas_temperature = _type_1(8)
    """The refrigerant returning to the compressor (AIN6)."""

    heat_exchanger_coil_temperature = _type_1(9)
    """The inner coil pipe, in the titanium heat exchanger (AIN4)."""

    compressor_frequency = integer(10, signed=False, unit="Hz")
    """The frequency the compressor runs at."""

    compressor_current = gauge(11, 0.1, signed=False, unit="A")
    """The compressor current."""

    cooling_plate_temperature = _type_2(12)
    """The heat sink of the inverter board."""

    expansion_valve_opening = integer(13, signed=False)
    """The opening of the electronic expansion valve, in steps."""

    fan_speed = integer(14, signed=False, unit="rpm")
    """The speed of the DC fan motor."""


class Settings(Component):
    """Function, working mode, setpoints and service parameters (FC03 / FC06).

    Holding 11 is reserved, and claimed only so the 9 to 11 block is read
    whole. The EEV overheat levels (17 and 18) and the power-off restart
    memory (25) are not modelled: the last one sits in a block the table does
    not allow a read to start in.
    """

    register_ranges = ((0, 2), (3, 5), (6, 8), (9, 11))

    function = enum(0, Function, writable=member(Function))
    """Automatic, heating or cooling."""

    working_mode = enum(1, WorkingMode, writable=member(WorkingMode))
    """Smart, silence, super silence or turbo."""

    auto_target_temperature = _type_1(
        2, writable=whole_degrees(*TARGET_TEMPERATURE_LIMITS[Function.AUTO])
    )
    """The setpoint in automatic mode."""

    heating_target_temperature = _type_1(
        3, writable=whole_degrees(*TARGET_TEMPERATURE_LIMITS[Function.HEATING])
    )
    """The setpoint in heating mode."""

    cooling_target_temperature = _type_1(
        4, writable=whole_degrees(*TARGET_TEMPERATURE_LIMITS[Function.COOLING])
    )
    """The setpoint in cooling mode."""

    pump_mode = enum(5, PumpMode, writable=member(PumpMode))
    """When the board runs the filtration pump (P0)."""

    pump_run_time = integer(
        6, signed=False, writable=whole_number(10, 120, step=5), unit="min"
    )
    """How long the pump runs to sample the water temperature (P1)."""

    defrost_interval = integer(
        7, signed=False, writable=whole_number(30, 90), unit="min"
    )
    """How long the compressor must run before a defrost may start (P2)."""

    defrost_start_temperature = _type_1(8, writable=whole_degrees(-17, 0))
    """The coil temperature at or below which a defrost starts (P3)."""

    defrost_max_duration = integer(
        9, signed=False, writable=whole_number(1, 12), unit="min"
    )
    """The longest a defrost may last (P4)."""

    defrost_end_temperature = _type_1(10, writable=whole_degrees(8, 30))
    """The coil temperature at which a defrost ends (P5)."""

    _reserved_11 = raw_register(11)  # claimed so the 9 to 11 block is read whole

    def target_temperature(self, function: Function) -> float | None:
        """The setpoint of ``function``."""
        value: float | None = getattr(self, _TARGET_FIELDS[Function(function)])
        return value

    async def async_set_target_temperature(
        self, function: Function, temperature: float
    ) -> None:
        """Set the setpoint of ``function``, in whole degrees."""
        await self.write(_TARGET_FIELDS[Function(function)], temperature)


_TARGET_FIELDS = {
    Function.AUTO: "auto_target_temperature",
    Function.HEATING: "heating_target_temperature",
    Function.COOLING: "cooling_target_temperature",
}
