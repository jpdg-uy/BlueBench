"""Exceptions raised by the BlueBench API. All derive from BlueBenchError."""
from __future__ import annotations


class BlueBenchError(Exception):
    """Base class: catch this to handle any BlueBench failure."""


class BluetoothUnavailable(BlueBenchError):
    """No usable Bluetooth adapter (off, missing, or blocked by the OS)."""


class DeviceNotFound(BlueBenchError):
    """No advertising device matched within the scan time."""


class ConnectFailed(BlueBenchError):
    """The device was found (or addressed directly) but the connection failed."""


class NotConnected(BlueBenchError):
    """The operation needs a connection and the device is not (or no longer) connected."""


class GattError(BlueBenchError):
    """A GATT operation failed, or the characteristic does not exist or lacks the property."""


class PairingFailed(BlueBenchError):
    """Pairing (bonding) or removing the bond failed."""


class MacroFailed(BlueBenchError):
    """A macro could not be found or loaded, or failed with ``check=True``."""


class DfuFailed(BlueBenchError):
    """The firmware update failed or the package is invalid."""


class SnifferError(BlueBenchError):
    """The nRF Sniffer could not be opened or stopped responding."""
