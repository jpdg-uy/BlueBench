"""BlueBench Python API: use BlueBench from scripts, tests and other programs.

Async (asyncio)::

    import asyncio
    from bluebench import api

    async def main():
        for d in await api.scan(timeout=5, mfr="5900"):
            print(d.address, d.name, d.rssi)
        async with api.connect(mfr="5900") as dev:
            print(await dev.read("2a00"))
            await dev.write("6e400002-b5a3-f393-e0a9-e50e24dcca9e", b"hello")
            async with dev.notifications("2a37") as n:
                print(await n.get(timeout=5))

    asyncio.run(main())

Blocking, for plain scripts and pytest: ``from bluebench.api import sync``.

Everything that fails raises a ``BlueBenchError`` subclass (see ``errors``).
Progress and protocol details go to the standard ``logging`` logger "bluebench".
Bluetooth only works where the OS gives access to a radio (Windows, not WSL).
"""
from ..ble.adv_filter import DeviceFilter
from ..core.macros import Macro, Step
from .device import (Characteristic, Descriptor, Device, DfuResult, MacroResult, Notifications,
                     Service, StepResult, Uart, connect, is_address, load_macro)
from .errors import (BlueBenchError, BluetoothUnavailable, ConnectFailed, DeviceNotFound,
                     DfuFailed, GattError, MacroFailed, NotConnected, PairingFailed,
                     SnifferError)
from .scan import ScanResult, find, scan, scan_stream
from .sniff import SniffResult, find_sniffer, sniff, sniffer_ports

__all__ = [
    "scan", "scan_stream", "find", "connect", "sniff", "sniffer_ports", "find_sniffer", "load_macro",
    "is_address", "DeviceFilter", "Macro", "Step", "ScanResult", "Device", "Service",
    "Characteristic", "Descriptor", "Notifications", "Uart", "MacroResult", "StepResult", "DfuResult",
    "SniffResult", "BlueBenchError", "BluetoothUnavailable", "DeviceNotFound",
    "ConnectFailed", "NotConnected", "GattError", "PairingFailed", "MacroFailed",
    "DfuFailed", "SnifferError",
]
