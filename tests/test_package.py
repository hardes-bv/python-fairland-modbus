"""Tests for the package surface and its exception hierarchy."""

import pytest

import fairland_modbus
from fairland_modbus import FairlandConnectionError, FairlandError


def test_version_is_a_string() -> None:
    """The package exposes a non-empty version string."""
    assert isinstance(fairland_modbus.__version__, str)
    assert fairland_modbus.__version__


def test_public_names_are_exported() -> None:
    """Every name listed in __all__ is an attribute of the package."""
    for name in fairland_modbus.__all__:
        assert hasattr(fairland_modbus, name)


def test_connection_error_is_a_fairland_error() -> None:
    """A transport failure can be caught as the library's base error."""
    with pytest.raises(FairlandError):
        raise FairlandConnectionError("device unreachable")


def test_fairland_error_is_an_exception() -> None:
    """The base error derives from Exception and keeps its message."""
    error = FairlandError("bad value")

    assert isinstance(error, Exception)
    assert str(error) == "bad value"


def test_a_vendored_copy_still_has_a_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without distribution metadata, the package still imports."""
    import importlib
    import importlib.metadata

    def missing(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", missing)
    reloaded = importlib.reload(fairland_modbus)

    assert reloaded.__version__ == "0.0.0+vendored"
    monkeypatch.undo()
    importlib.reload(fairland_modbus)
