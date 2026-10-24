"""Exceptions raised by the Fairland Modbus client."""


class FairlandError(Exception):
    """Base error for the library: a bad value or undecodable data."""


class FairlandConnectionError(FairlandError):
    """The Modbus transport failed while talking to the device."""


class FairlandTimeoutError(FairlandConnectionError):
    """The link is up, but the device behind it did not answer.

    An RS485 gateway keeps its socket open while the device behind it is
    silent, so this is how a powered-off heat pump usually shows up.
    """


class FairlandRefusedError(FairlandError):
    """The device answered, but refused the request.

    Some boards refuse a value the register table documents, such as a working
    mode the model does not have.
    """


class NotAFairlandDeviceError(FairlandError):
    """The device answers Modbus, but not like the expected Fairland device."""
