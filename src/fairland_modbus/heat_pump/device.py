"""A heat pump with an MWH216 or MWH298 board, as one object over its blocks."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Final

from modbus_connection import (
    GatewayPathUnavailableError,
    GatewayTargetError,
    ModbusError,
    ModbusExceptionError,
    ModbusTimeoutError,
)
from modbus_connection.model import Device, Raw, UpdateReport

from ..exceptions import (
    FairlandConnectionError,
    FairlandError,
    FairlandRefusedError,
    FairlandTimeoutError,
    NotAFairlandDeviceError,
)
from .components import Controls, Readings, Settings, Status
from .const import (
    MESSAGE_SPACING,
    TARGET_TEMPERATURE_LIMITS,
    Function,
    PumpMode,
    WorkingMode,
)

if TYPE_CHECKING:
    from modbus_connection import ModbusUnit

# What one poll reads, by attribute name.
POLLED: Final = ("controls", "status", "readings", "settings")

# The service parameters a caller may change through ``async_set_setting``.
SERVICE_SETTINGS: Final = frozenset(
    {
        "pump_run_time",
        "defrost_interval",
        "defrost_start_temperature",
        "defrost_max_duration",
        "defrost_end_temperature",
    }
)

# A gateway in "Modbus TCP to RTU" mode answers with one of these when the
# board behind it stays silent: the heat pump is off or unwired.
_SILENT_BEHIND_GATEWAY = (GatewayPathUnavailableError, GatewayTargetError)

# Seconds to wait before the second read back of a working mode. Some boards
# acknowledge a working mode write and keep their mode, without an error.
WORKING_MODE_SETTLE: Final = 1.0

# A pool water temperature, in degrees, that tells this board from another
# Modbus device answering at the same address.
_PLAUSIBLE_WATER = (-20.0, 60.0)


@contextmanager
def _translated_errors() -> Iterator[None]:
    """Turn transport and device errors into the library's own exceptions."""
    try:
        yield
    except _SILENT_BEHIND_GATEWAY as err:
        raise FairlandTimeoutError(f"the heat pump does not answer: {err}") from err
    except ModbusExceptionError as err:
        raise FairlandRefusedError(f"the heat pump refused the request: {err}") from err
    except ModbusTimeoutError as err:
        raise FairlandTimeoutError(f"the heat pump does not answer: {err}") from err
    except ModbusError as err:
        raise FairlandConnectionError(f"cannot reach the heat pump: {err}") from err
    except ValueError as err:
        raise FairlandError(str(err)) from err


class FairlandHeatPump(Device):
    """A Fairland inverter pool heat pump driven by an MWH216 or MWH298 board.

    The same board sits in heat pumps sold under other brands. The board
    reports no model or serial number, so the caller names the unit.
    """

    def __init__(self, unit: ModbusUnit) -> None:
        super().__init__(unit)
        unit.set_message_spacing(MESSAGE_SPACING)
        self.controls = Controls(unit)
        self.status = Status(unit)
        self.readings = Readings(unit)
        self.settings = Settings(unit)
        # Values written since the last poll of their block. A write does not
        # refresh the decoded value, and a command sent right after another
        # must build on it: a setpoint goes to the register of the function
        # just chosen, and a switch-on follows a switch-off not polled yet.
        self._written_function: Function | None = None
        self._written_power: bool | None = None
        self.working_mode_settle = WORKING_MODE_SETTLE

    @property
    def function(self) -> Function | None:
        """The function the heat pump is in, counting a write not polled yet."""
        return self._written_function or self.settings.function

    @property
    def power(self) -> bool | None:
        """Whether the heat pump is on, counting a write not polled yet."""
        if self._written_power is not None:
            return self._written_power
        return self.controls.power

    @property
    def target_temperature(self) -> float | None:
        """The setpoint of the function the heat pump is in."""
        function = self.function
        if function is None:
            return None
        return self.settings.target_temperature(function)

    @staticmethod
    def target_temperature_limits(function: Function) -> tuple[int, int]:
        """The setpoint range, in whole degrees, that ``function`` accepts."""
        return TARGET_TEMPERATURE_LIMITS[Function(function)]

    async def async_validate(self) -> None:
        """Check the device answers like this board, or raise.

        Raises ``NotAFairlandDeviceError`` when the device refuses the board's
        registers or its values make no sense for the board,
        ``FairlandTimeoutError`` when nothing answers behind a live link, and
        ``FairlandConnectionError`` when the link fails.
        """
        try:
            with _translated_errors():
                await self.readings.async_update(notify=False)
                await self.settings.async_update(notify=False)
        except FairlandRefusedError as err:
            # Another Fairland board, like the PC1004, answers every read of
            # this table with an exception response.
            raise NotAFairlandDeviceError(
                f"the device refuses the registers of this board: {err}"
            ) from err

        settings = self.settings
        if settings.function is None or settings.working_mode is None:
            raise NotAFairlandDeviceError("unexpected function or working mode")
        low, high = TARGET_TEMPERATURE_LIMITS[Function.HEATING]
        heating = settings.heating_target_temperature
        if heating is None or not low <= heating <= high:
            raise NotAFairlandDeviceError(f"unexpected heating setpoint {heating!r}")
        water = self.readings.inlet_water_temperature
        if water is None or not _PLAUSIBLE_WATER[0] <= water <= _PLAUSIBLE_WATER[1]:
            raise NotAFairlandDeviceError(f"unexpected water temperature {water!r}")

    async def async_update(self) -> UpdateReport:
        """Refresh every block, each on its own.

        A block that fails lands in the report. Raises ``FairlandTimeoutError``
        when nothing answers, and ``FairlandConnectionError`` when the link
        fails.
        """
        with _translated_errors():
            report = await self.async_poll(POLLED)
        if "settings" in report.updated:
            self._written_function = None
        if "controls" in report.updated:
            self._written_power = None
        if not report.updated and all(
            isinstance(err, _SILENT_BEHIND_GATEWAY) for err in report.failed.values()
        ):
            raise FairlandTimeoutError("the gateway reports the heat pump is silent")
        return report

    async def async_read_raw(self, names: Iterable[str] = POLLED) -> Raw:
        """Every register this library reads, undecoded, for diagnostics."""
        with _translated_errors():
            return await super().async_read_raw(names)

    async def async_set_power(self, on: bool) -> None:
        """Switch the heat pump on or off."""
        with _translated_errors():
            await self.controls.async_set_power(on)
        self._written_power = bool(on)

    async def async_start_defrost(self) -> None:
        """Ask the board to defrost now."""
        with _translated_errors():
            await self.controls.async_start_defrost()

    async def async_set_function(self, function: Function) -> None:
        """Switch between automatic, heating and cooling."""
        with _translated_errors():
            await self.settings.write("function", function)
        self._written_function = Function(function)

    async def async_set_working_mode(self, mode: WorkingMode) -> None:
        """Choose smart, silence, super silence or turbo.

        Raises ``FairlandRefusedError`` when the board refuses the mode. Some
        boards answer with an exception, others acknowledge the write and keep
        their mode, so the mode is read back. Some accept a mode only while
        the compressor runs.
        """
        with _translated_errors():
            await self.settings.write("working_mode", mode)
        mode = WorkingMode(mode)
        for attempt in range(2):
            if attempt:
                await asyncio.sleep(self.working_mode_settle)
            try:
                with _translated_errors():
                    await self.settings.async_update(notify=False)
            except FairlandError:
                # The write went through and the next poll tells the truth.
                return
            if self.settings.working_mode is mode:
                return
        kept = self.settings.working_mode
        raise FairlandRefusedError(
            f"the heat pump kept the working mode "
            f"{kept.name.lower() if kept is not None else 'unknown'} "
            f"instead of {mode.name.lower()}"
        )

    async def async_set_target_temperature(
        self, temperature: float, function: Function | None = None
    ) -> None:
        """Set the setpoint of ``function``, or of the current function."""
        if function is None:
            function = self.function
            if function is None:
                raise FairlandError("the current function is not known yet")
        with _translated_errors():
            await self.settings.async_set_target_temperature(function, temperature)

    async def async_set_pump_mode(self, mode: PumpMode) -> None:
        """Choose when the board runs the filtration pump (P0)."""
        with _translated_errors():
            await self.settings.write("pump_mode", mode)

    async def async_set_setting(self, name: str, value: float) -> None:
        """Change one of the service parameters in ``SERVICE_SETTINGS``."""
        if name not in SERVICE_SETTINGS:
            raise FairlandError(f"{name!r} is not a service parameter")
        with _translated_errors():
            await self.settings.write(name, value)
