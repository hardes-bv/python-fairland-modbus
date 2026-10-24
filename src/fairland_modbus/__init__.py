"""Asynchronous Modbus client for Fairland inverter pumps and heat pumps."""

from importlib.metadata import PackageNotFoundError, version

from .exceptions import (
    FairlandConnectionError,
    FairlandError,
    FairlandRefusedError,
    FairlandTimeoutError,
    NotAFairlandDeviceError,
)

# The release workflow stamps the version into pyproject.toml from the git
# tag, so the installed distribution metadata is the single source of truth.
# A copy vendored inside a Home Assistant custom integration has none.
try:
    __version__ = version("fairland-modbus")
except PackageNotFoundError:
    __version__ = "0.0.0+vendored"

__all__ = [
    "FairlandConnectionError",
    "FairlandError",
    "FairlandRefusedError",
    "FairlandTimeoutError",
    "NotAFairlandDeviceError",
    "__version__",
]
