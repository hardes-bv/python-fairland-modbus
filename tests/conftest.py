"""Fixtures for the fairland-modbus tests."""

from __future__ import annotations

import pytest
from modbus_connection.mock import MockModbusUnit

from fairland_modbus.heat_pump import FairlandHeatPump
from fairland_modbus.heat_pump.testing import load_image


@pytest.fixture
def unit(mock_modbus_unit: MockModbusUnit) -> MockModbusUnit:
    """A mock unit seeded like a heat pump heating a pool."""
    load_image(mock_modbus_unit)
    return mock_modbus_unit


@pytest.fixture
def heat_pump(unit: MockModbusUnit) -> FairlandHeatPump:
    """A heat pump on the seeded mock unit."""
    return FairlandHeatPump(unit)
