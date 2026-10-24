"""Tests for the MWH216 and MWH298 heat pump, on the modbus-connection mock."""

from __future__ import annotations

import pytest
from modbus_connection import (
    GatewayTargetError,
    IllegalDataAddressError,
    IllegalDataValueError,
    IllegalFunctionError,
    ModbusConnectionError,
    ModbusTimeoutError,
)
from modbus_connection.mock import MockModbusUnit, WriteEvent

from fairland_modbus import (
    FairlandConnectionError,
    FairlandError,
    FairlandRefusedError,
    FairlandTimeoutError,
    NotAFairlandDeviceError,
)
from fairland_modbus.heat_pump import (
    FAULT_CODES,
    MESSAGE_SPACING,
    SERVICE_SETTINGS,
    FairlandHeatPump,
    FaultKind,
    Function,
    PumpMode,
    WorkingMode,
    fault_kind,
)
from fairland_modbus.heat_pump.components import member, whole_number
from fairland_modbus.heat_pump.testing import ALLOWED_READS, WRITABLE


def _writes(unit: MockModbusUnit) -> list[WriteEvent]:
    events: list[WriteEvent] = []
    unit.on_write(events.append)
    return events


async def test_decodes_the_readings(heat_pump: FairlandHeatPump) -> None:
    """Input registers decode with the two temperature types of the table."""
    report = await heat_pump.async_update()

    assert report.complete
    r = heat_pump.readings
    assert r.compressor_speed == 45
    assert r.compressor_target_frequency == 52
    assert r.pfc_voltage == 380
    assert r.inlet_water_temperature == 26.0
    assert r.outlet_water_temperature == 28.0
    assert r.ambient_temperature == 20.5
    assert r.exhaust_gas_temperature == 70.0
    assert r.evaporator_coil_temperature == 12.0
    assert r.suction_gas_temperature == 16.0
    assert r.heat_exchanger_coil_temperature == 29.0
    assert r.compressor_frequency == 48
    assert r.compressor_current == 4.2
    assert r.cooling_plate_temperature == 45.0
    assert r.expansion_valve_opening == 320
    assert r.fan_speed == 650


async def test_decodes_the_settings(heat_pump: FairlandHeatPump) -> None:
    """Holding registers decode to enums and whole-degree setpoints."""
    await heat_pump.async_update()

    s = heat_pump.settings
    assert s.function is Function.HEATING
    assert s.working_mode is WorkingMode.SMART
    assert s.auto_target_temperature == 26.0
    assert s.heating_target_temperature == 28.0
    assert s.cooling_target_temperature == 26.0
    assert s.pump_mode is PumpMode.CONTINUOUS
    assert s.pump_run_time == 60
    assert s.defrost_interval == 30
    assert s.defrost_start_temperature == -7.0
    assert s.defrost_max_duration == 12
    assert s.defrost_end_temperature == 13.0


async def test_decodes_the_status(heat_pump: FairlandHeatPump) -> None:
    """Coils and discrete inputs decode to the running state and terminals."""
    await heat_pump.async_update()

    assert heat_pump.controls.power is True
    s = heat_pump.status
    assert s.running is True
    assert s.defrosting is False
    assert s.malfunction is False
    assert s.compressor_demand is True
    assert s.digital_input(2) is True
    assert s.remote_switch_closed is True
    assert s.flow_switch_closed is True
    assert s.digital_input(1) is False
    assert s.digital_output(1) is True
    assert s.digital_output(9) is False
    assert s.active_codes == ()
    assert s.no_water_flow is False


async def test_reads_only_the_blocks_the_table_allows(
    heat_pump: FairlandHeatPump, unit: MockModbusUnit
) -> None:
    """Every read starts at an allowed address and has the fixed length."""
    await heat_pump.async_update()
    await heat_pump.async_read_raw()

    assert unit.read_events
    for event in unit.read_events:
        assert (event.address, event.count) in ALLOWED_READS[event.register_type]


async def test_one_poll_takes_twelve_requests(
    heat_pump: FairlandHeatPump, unit: MockModbusUnit
) -> None:
    """A poll reads 1 coil block, 2 bit blocks, 5 input and 4 holding blocks."""
    await heat_pump.async_update()

    assert len(unit.read_events) == 12


def test_spaces_requests_as_the_table_asks(unit: MockModbusUnit) -> None:
    """The board needs at least 60 ms between two requests."""
    FairlandHeatPump(unit)

    assert unit.message_spacing == MESSAGE_SPACING == 0.06


async def test_reports_active_codes_in_table_order(
    heat_pump: FairlandHeatPump, unit: MockModbusUnit
) -> None:
    """Active codes come back in the order the table lists them."""
    unit.discrete.update({16: True, 51: True, 61: True, 64: True, 91: True})

    await heat_pump.async_update()

    s = heat_pump.status
    assert s.malfunction is True
    assert s.active_codes == ("E3", "Ed", "P0", "Fb")
    assert s.no_water_flow is True
    assert s.code_active("E1") is False


async def test_an_open_flow_switch_reads_without_a_fault(
    heat_pump: FairlandHeatPump, unit: MockModbusUnit
) -> None:
    """An open DIN3 reads as no flow even before the board raises E3."""
    unit.discrete.update({4: False})

    await heat_pump.async_update()

    assert heat_pump.status.flow_switch_closed is False
    assert heat_pump.status.no_water_flow is False


def test_lists_every_code_of_the_table() -> None:
    """The table has 12 E codes plus Ed, 11 P codes and 12 F codes."""
    assert len(FAULT_CODES) == 36
    assert FAULT_CODES[51] == "E3"
    assert FAULT_CODES[59] == "Eb"
    assert FAULT_CODES[61] == "Ed"
    assert FAULT_CODES[74] == "PA"
    assert FAULT_CODES[91] == "Fb"


def test_tells_protections_from_failures() -> None:
    """Protections clear by themselves; everything else is a failure."""
    assert fault_kind("E3") is FaultKind.PROTECTION
    assert fault_kind("Ed") is FaultKind.PROTECTION
    assert fault_kind("E1") is FaultKind.FAILURE
    assert fault_kind("F9") is FaultKind.FAILURE


@pytest.mark.parametrize("call", ["digital_input", "digital_output", "code_active"])
def test_rejects_unknown_terminals_and_codes(
    heat_pump: FairlandHeatPump, call: str
) -> None:
    """Asking for a terminal or code the board lacks raises ValueError."""
    with pytest.raises(ValueError):
        getattr(heat_pump.status, call)(0 if call != "code_active" else "E9x")


async def test_target_temperature_follows_the_function(
    heat_pump: FairlandHeatPump, unit: MockModbusUnit
) -> None:
    """The current setpoint is the one of the function the pump is in."""
    assert heat_pump.target_temperature is None

    await heat_pump.async_update()
    assert heat_pump.target_temperature == 28.0

    unit.holding[0] = Function.COOLING
    unit.holding[4] = 110
    await heat_pump.async_update()
    assert heat_pump.target_temperature == 25.0


async def test_a_bare_setpoint_follows_a_function_just_written(
    heat_pump: FairlandHeatPump, unit: MockModbusUnit
) -> None:
    """After a function change, the setpoint goes to the new function."""
    await heat_pump.async_update()
    writes = _writes(unit)

    await heat_pump.async_set_function(Function.COOLING)
    assert heat_pump.function is Function.COOLING
    await heat_pump.async_set_target_temperature(25)

    assert [(w.address, w.values) for w in writes] == [(0, [2]), (4, [110])]


async def test_a_poll_forgets_the_written_function(
    heat_pump: FairlandHeatPump, unit: MockModbusUnit
) -> None:
    """Once the settings are read again, the board is the reference."""
    await heat_pump.async_set_function(Function.COOLING)
    unit.holding[0] = Function.AUTO

    await heat_pump.async_update()

    assert heat_pump.function is Function.AUTO


async def test_keeps_the_written_function_while_settings_fail(
    heat_pump: FairlandHeatPump, unit: MockModbusUnit
) -> None:
    """A poll that misses the settings keeps the function just written."""
    await heat_pump.async_set_function(Function.COOLING)
    unit.fail_read(0, IllegalDataAddressError(), register_type="holding")

    await heat_pump.async_update()

    assert heat_pump.function is Function.COOLING


async def test_power_follows_a_write_until_the_next_poll(
    heat_pump: FairlandHeatPump, unit: MockModbusUnit
) -> None:
    """A switch-off counts at once, and the board is the reference again after."""
    await heat_pump.async_update()
    assert heat_pump.power is True

    await heat_pump.async_set_power(False)
    assert heat_pump.power is False

    unit.coil[0] = True
    await heat_pump.async_update()
    assert heat_pump.power is True


async def test_keeps_the_written_power_while_controls_fail(
    heat_pump: FairlandHeatPump, unit: MockModbusUnit
) -> None:
    """A poll that misses the coils keeps the state just written."""
    await heat_pump.async_set_power(False)
    unit.fail_read(0, IllegalDataAddressError(), register_type="coil")

    await heat_pump.async_update()

    assert heat_pump.power is False


def test_gives_the_setpoint_range_of_each_function() -> None:
    """Each function has its own setpoint range, from the table."""
    assert FairlandHeatPump.target_temperature_limits(Function.AUTO) == (12, 40)
    assert FairlandHeatPump.target_temperature_limits(Function.HEATING) == (18, 40)
    assert FairlandHeatPump.target_temperature_limits(Function.COOLING) == (12, 30)


async def test_switches_power_with_coil_0(
    heat_pump: FairlandHeatPump, unit: MockModbusUnit
) -> None:
    """Power on and off write coil 0 with FC05."""
    writes = _writes(unit)

    await heat_pump.async_set_power(False)
    await heat_pump.async_set_power(True)

    assert [
        (w.register_type, w.address, w.values, w.function_code) for w in writes
    ] == [
        ("coil", 0, [False], 5),
        ("coil", 0, [True], 5),
    ]


async def test_starts_a_defrost_with_coil_1(
    heat_pump: FairlandHeatPump, unit: MockModbusUnit
) -> None:
    """A forced defrost writes 1 to coil 1."""
    writes = _writes(unit)

    await heat_pump.async_start_defrost()

    assert [(w.register_type, w.address, w.values) for w in writes] == [
        ("coil", 1, [True])
    ]


async def test_writes_function_and_working_mode(
    heat_pump: FairlandHeatPump, unit: MockModbusUnit
) -> None:
    """Function and working mode write holding 0 and 1 with FC06."""
    writes = _writes(unit)

    await heat_pump.async_set_function(Function.COOLING)
    await heat_pump.async_set_working_mode(WorkingMode.SUPER_SILENCE)
    await heat_pump.async_set_pump_mode(PumpMode.WATER_TEMPERATURE)

    assert [(w.address, w.values, w.function_code) for w in writes] == [
        (0, [2], 6),
        (1, [2], 6),
        (5, [1], 6),
    ]


async def test_writes_setpoints_as_type_1_temperatures(
    heat_pump: FairlandHeatPump, unit: MockModbusUnit
) -> None:
    """A setpoint in degrees is written as (degrees + 30) * 2."""
    await heat_pump.async_update()
    writes = _writes(unit)

    await heat_pump.async_set_target_temperature(30)
    await heat_pump.async_set_target_temperature(12, Function.AUTO)
    await heat_pump.async_set_target_temperature(30.0, Function.COOLING)

    assert [(w.address, w.values) for w in writes] == [
        (3, [120]),
        (2, [84]),
        (4, [120]),
    ]


@pytest.mark.parametrize(
    ("function", "temperature"),
    [
        (Function.HEATING, 17),
        (Function.HEATING, 41),
        (Function.COOLING, 31),
        (Function.AUTO, 11),
        (Function.HEATING, 28.5),
        (Function.HEATING, "28"),
        (Function.HEATING, True),
    ],
)
async def test_refuses_setpoints_the_table_does_not_allow(
    heat_pump: FairlandHeatPump,
    unit: MockModbusUnit,
    function: Function,
    temperature: object,
) -> None:
    """Out of range, half or non-numeric setpoints never reach the board."""
    writes = _writes(unit)

    with pytest.raises(FairlandError):
        await heat_pump.async_set_target_temperature(temperature, function)  # type: ignore[arg-type]

    assert writes == []


async def test_needs_the_function_before_a_bare_setpoint(
    heat_pump: FairlandHeatPump,
) -> None:
    """Without a first poll there is no current function to set."""
    with pytest.raises(FairlandError, match="not known yet"):
        await heat_pump.async_set_target_temperature(28)


@pytest.mark.parametrize(
    ("name", "value", "address", "raw"),
    [
        ("pump_run_time", 45, 6, 45),
        ("defrost_interval", 90, 7, 90),
        ("defrost_start_temperature", -17, 8, 26),
        ("defrost_max_duration", 1, 9, 1),
        ("defrost_end_temperature", 30, 10, 120),
    ],
)
async def test_writes_service_parameters(
    heat_pump: FairlandHeatPump,
    unit: MockModbusUnit,
    name: str,
    value: int,
    address: int,
    raw: int,
) -> None:
    """Each service parameter writes its own holding register."""
    writes = _writes(unit)

    await heat_pump.async_set_setting(name, value)

    assert [(w.address, w.values) for w in writes] == [(address, [raw])]


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("pump_run_time", 42),
        ("pump_run_time", 125),
        ("defrost_interval", 29),
        ("defrost_start_temperature", 1),
        ("defrost_max_duration", 13),
        ("defrost_end_temperature", 7),
        ("function", 1),
    ],
)
async def test_refuses_bad_service_parameters(
    heat_pump: FairlandHeatPump, unit: MockModbusUnit, name: str, value: int
) -> None:
    """Out of range, off-step and unknown parameters raise FairlandError."""
    writes = _writes(unit)

    with pytest.raises(FairlandError):
        await heat_pump.async_set_setting(name, value)

    assert writes == []


@pytest.mark.parametrize("value", [7, True, "smart"])
async def test_refuses_unknown_working_modes(
    heat_pump: FairlandHeatPump, value: object
) -> None:
    """Only the members of WorkingMode are written."""
    with pytest.raises(FairlandError, match="WorkingMode"):
        await heat_pump.async_set_working_mode(value)  # type: ignore[arg-type]


def test_validators_reject_what_is_not_a_number() -> None:
    """The validators turn a wrong type into ValueError."""
    with pytest.raises(ValueError, match="number"):
        whole_number(0, 10)(None)
    with pytest.raises(ValueError, match="whole"):
        whole_number(0, 10)(2.5)
    assert member(PumpMode)(2) is PumpMode.TIME_AND_WATER_TEMPERATURE


async def test_writes_only_documented_addresses(
    heat_pump: FairlandHeatPump, unit: MockModbusUnit
) -> None:
    """No call can write the factory reset coil or a register beyond P5."""
    await heat_pump.async_update()
    writes = _writes(unit)

    await heat_pump.async_set_power(True)
    await heat_pump.async_start_defrost()
    await heat_pump.async_set_function(Function.AUTO)
    await heat_pump.async_set_working_mode(WorkingMode.TURBO)
    for function in Function:
        await heat_pump.async_set_target_temperature(26, function)
    await heat_pump.async_set_pump_mode(PumpMode.CONTINUOUS)
    for name in SERVICE_SETTINGS:
        low = {"pump_run_time": 10, "defrost_interval": 30, "defrost_max_duration": 1}
        await heat_pump.async_set_setting(
            name, low.get(name, -10 if "start" in name else 10)
        )

    assert {(w.register_type, w.address) for w in writes} == {
        *(("coil", a) for a in WRITABLE["coil"]),
        *(("holding", a) for a in WRITABLE["holding"]),
    }


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (IllegalDataValueError(), FairlandRefusedError),
        (GatewayTargetError(), FairlandTimeoutError),
        (ModbusTimeoutError("silent"), FairlandTimeoutError),
        (ModbusConnectionError("refused"), FairlandConnectionError),
    ],
)
async def test_translates_write_errors(
    heat_pump: FairlandHeatPump,
    unit: MockModbusUnit,
    error: Exception,
    expected: type[Exception],
) -> None:
    """A failed write surfaces as one of the library's exceptions."""
    unit.fail_write(1, error)

    with pytest.raises(expected):
        await heat_pump.async_set_working_mode(WorkingMode.SUPER_SILENCE)


async def test_a_refused_working_mode_is_reported_as_such(
    heat_pump: FairlandHeatPump, unit: MockModbusUnit
) -> None:
    """A board without a mode refuses it, and the caller can tell."""
    unit.fail_write(1, IllegalDataValueError())

    with pytest.raises(FairlandRefusedError, match="refused"):
        await heat_pump.async_set_working_mode(WorkingMode.TURBO)


async def test_a_failing_block_lands_in_the_report(
    heat_pump: FairlandHeatPump, unit: MockModbusUnit
) -> None:
    """One refused block fails its component only."""
    unit.fail_read(12, IllegalDataAddressError(), register_type="input")

    report = await heat_pump.async_update()

    assert report.updated == {"controls", "status", "settings"}
    assert set(report.failed) == {"readings"}


async def test_a_silent_board_behind_a_gateway_raises_a_timeout(
    heat_pump: FairlandHeatPump, unit: MockModbusUnit
) -> None:
    """A gateway answering 0B for every block means the pump is silent."""
    unit.fail_requests(GatewayTargetError())

    with pytest.raises(FairlandTimeoutError, match="silent"):
        await heat_pump.async_update()


async def test_a_dead_link_raises_a_connection_error(
    heat_pump: FairlandHeatPump, unit: MockModbusUnit
) -> None:
    """A transport failure is raised, not reported per block."""
    unit.fail_requests(ModbusConnectionError("refused"))

    with pytest.raises(FairlandConnectionError):
        await heat_pump.async_update()


async def test_a_silent_link_raises_a_timeout(
    heat_pump: FairlandHeatPump, unit: MockModbusUnit
) -> None:
    """Nothing answering at all raises FairlandTimeoutError."""
    unit.fail_requests(ModbusTimeoutError("silent"))

    with pytest.raises(FairlandTimeoutError):
        await heat_pump.async_update()


async def test_validates_a_heat_pump(heat_pump: FairlandHeatPump) -> None:
    """The seeded image passes validation."""
    await heat_pump.async_validate()

    assert heat_pump.settings.function is Function.HEATING


@pytest.mark.parametrize(
    ("space", "address", "value", "match"),
    [
        ("holding", 3, 200, "heating setpoint"),
        ("input", 3, 0, "water temperature"),
        ("input", 3, 220, "water temperature"),
    ],
)
async def test_refuses_a_device_that_is_not_this_board(
    heat_pump: FairlandHeatPump,
    unit: MockModbusUnit,
    space: str,
    address: int,
    value: int,
    match: str,
) -> None:
    """Values outside what the board can report mean another device."""
    getattr(unit, space)[address] = value

    with pytest.raises(NotAFairlandDeviceError, match=match):
        await heat_pump.async_validate()


async def test_an_unknown_function_fails_validation(
    heat_pump: FairlandHeatPump, unit: MockModbusUnit
) -> None:
    """A function code the table does not list is not this board."""
    unit.holding[0] = 9

    with pytest.raises(NotAFairlandDeviceError, match="function"):
        await heat_pump.async_validate()


@pytest.mark.parametrize("error", [IllegalFunctionError(), IllegalDataAddressError()])
async def test_a_board_refusing_the_table_is_not_this_board(
    heat_pump: FairlandHeatPump, unit: MockModbusUnit, error: Exception
) -> None:
    """A device refusing every read, like a PC1004 board, is not this board."""
    unit.fail_requests(error)

    with pytest.raises(NotAFairlandDeviceError, match="refuses"):
        await heat_pump.async_validate()


async def test_validation_translates_transport_errors(
    heat_pump: FairlandHeatPump, unit: MockModbusUnit
) -> None:
    """A silent link during validation raises FairlandTimeoutError."""
    unit.fail_requests(ModbusTimeoutError("silent"))

    with pytest.raises(FairlandTimeoutError):
        await heat_pump.async_validate()


async def test_reads_the_raw_map_for_diagnostics(
    heat_pump: FairlandHeatPump,
) -> None:
    """The raw map holds every block the library reads, placeholders included."""
    raw = await heat_pump.async_read_raw()

    assert len(raw["coil"]) == 48
    assert len(raw["discrete"]) == 96
    assert sorted(raw["input"]) == list(range(15))
    assert sorted(raw["holding"]) == list(range(12))
    assert raw["holding"][3] == 116


async def test_raw_reads_translate_errors(
    heat_pump: FairlandHeatPump, unit: MockModbusUnit
) -> None:
    """A failed raw read surfaces as a library exception."""
    unit.fail_requests(ModbusConnectionError("refused"))

    with pytest.raises(FairlandConnectionError):
        await heat_pump.async_read_raw()


def _keep_working_mode(unit: MockModbusUnit, kept: int, writes_left: int = 0) -> None:
    """Make the mock board acknowledge working mode writes and keep ``kept``."""

    def keep(event: WriteEvent) -> None:
        nonlocal writes_left
        if event.register_type == "holding" and event.address == 1:
            if writes_left:
                writes_left -= 1
            else:
                unit.holding[1] = kept

    unit.on_write(keep)


async def test_a_working_mode_kept_silently_is_reported_as_refused(
    heat_pump: FairlandHeatPump, unit: MockModbusUnit
) -> None:
    """A board that acknowledges the write and keeps its mode is caught."""
    heat_pump.working_mode_settle = 0
    _keep_working_mode(unit, WorkingMode.SILENCE)

    with pytest.raises(FairlandRefusedError, match="kept the working mode silence"):
        await heat_pump.async_set_working_mode(WorkingMode.TURBO)

    assert heat_pump.settings.working_mode is WorkingMode.SILENCE


async def test_a_working_mode_applied_late_is_accepted(
    heat_pump: FairlandHeatPump, unit: MockModbusUnit
) -> None:
    """A mode that shows up at the second read back counts as accepted."""
    heat_pump.working_mode_settle = 0
    reads = 0
    original = unit.read_holding_registers

    async def read(address: int, count: int) -> list[int]:
        nonlocal reads
        if address == 0:
            reads += 1
            late = WorkingMode.SILENCE if reads == 1 else WorkingMode.TURBO
            unit.holding[1] = late
        return await original(address, count)

    unit.read_holding_registers = read  # type: ignore[method-assign]

    await heat_pump.async_set_working_mode(WorkingMode.TURBO)

    assert reads == 2
    assert heat_pump.settings.working_mode is WorkingMode.TURBO


async def test_a_working_mode_kept_unreadable_is_reported_as_unknown(
    heat_pump: FairlandHeatPump, unit: MockModbusUnit
) -> None:
    """A read back outside the enum still reports a refusal."""
    heat_pump.working_mode_settle = 0
    _keep_working_mode(unit, 9)

    with pytest.raises(FairlandRefusedError, match="kept the working mode unknown"):
        await heat_pump.async_set_working_mode(WorkingMode.TURBO)


async def test_a_failed_read_back_trusts_the_acknowledged_write(
    heat_pump: FairlandHeatPump, unit: MockModbusUnit
) -> None:
    """When the read back fails, the write stands and the next poll decides."""
    unit.fail_read(0, ModbusTimeoutError("silent"), register_type="holding")

    await heat_pump.async_set_working_mode(WorkingMode.TURBO)

    assert unit.holding[1] == WorkingMode.TURBO
