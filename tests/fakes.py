"""In-memory stand-ins for bleak's client and GATT objects."""
from __future__ import annotations

from dataclasses import dataclass, field

from bluebench.ble import uuids


@dataclass
class FakeDescriptor:
    uuid: str
    handle: int


@dataclass
class FakeChar:
    uuid: str
    handle: int
    properties: list[str]
    descriptors: list[FakeDescriptor] = field(default_factory=list)
    value: bytes = b""
    max_write_without_response_size: int = 244


@dataclass
class FakeService:
    uuid: str
    handle: int
    characteristics: list[FakeChar]


class FakeServices:
    def __init__(self, services: list[FakeService]) -> None:
        self._services = services

    def __iter__(self):
        return iter(self._services)

    def get_characteristic(self, handle: int):
        for s in self._services:
            for c in s.characteristics:
                if c.handle == handle:
                    return c
        return None


def default_gatt() -> list[FakeService]:
    return [
        FakeService(uuids.normalize("1800"), 1, [
            FakeChar(uuids.normalize("2a00"), 3, ["read", "write"], value=b"Fake HRM"),
        ]),
        FakeService(uuids.normalize("180d"), 10, [
            FakeChar(uuids.normalize("2a37"), 12, ["notify"],
                     [FakeDescriptor(uuids.normalize("2902"), 13)]),
            FakeChar(uuids.normalize("2a38"), 15, ["read"], value=b"\x01"),
        ]),
        FakeService("6e400001-b5a3-f393-e0a9-e50e24dcca9e", 20, [
            FakeChar("6e400002-b5a3-f393-e0a9-e50e24dcca9e", 22, ["write", "write-without-response"]),
            FakeChar("6e400003-b5a3-f393-e0a9-e50e24dcca9e", 24, ["notify"],
                     [FakeDescriptor(uuids.normalize("2902"), 25)]),
        ]),
    ]


NUS_RX_HANDLE = 22
NUS_TX_HANDLE = 24


class FakeClient:
    """Mimics the subset of BleakClient that DeviceSession uses."""

    instances: list["FakeClient"] = []
    fail_connect = False
    fail_pair = False
    unpaired: list = []
    gatt_factory = staticmethod(default_gatt)

    def __init__(self, device, disconnected_callback=None, timeout=10.0) -> None:
        self.device = device
        self._dc = disconnected_callback
        self.is_connected = False
        self.services = FakeServices(self.gatt_factory())
        self.mtu_size = 247
        self.writes: list[tuple[int, bytes, bool]] = []
        self.notify_callbacks: dict[int, object] = {}
        self.paired = False
        FakeClient.instances.append(self)

    async def connect(self) -> bool:
        if FakeClient.fail_connect:
            raise OSError("Device not found")  # like BleakDeviceNotFoundError; not a timeout
        self.is_connected = True
        return True

    async def pair(self) -> None:
        if FakeClient.fail_pair:
            raise RuntimeError("Could not pair with device: AUTHENTICATION_FAILURE")
        self.paired = True

    async def unpair(self) -> None:
        self.paired = False
        FakeClient.unpaired.append(self.device)
        if self.is_connected:  # like WinRT: removing the bond drops the link
            await self.disconnect()

    async def disconnect(self) -> bool:
        self.is_connected = False
        if self._dc:
            self._dc(self)
        return True

    async def read_gatt_char(self, ch):
        return bytearray(ch.value)

    async def write_gatt_char(self, ch, data, response=True):
        if "write" not in ch.properties and response:
            raise RuntimeError("Write not permitted")
        if len(data) > ch.max_write_without_response_size:
            raise RuntimeError("Write exceeds the maximum length")
        ch.value = bytes(data)
        self.writes.append((ch.handle, bytes(data), response))
        # Behave like Nordic's peripheral_uart sample: echo RX writes on TX.
        if ch.handle == NUS_RX_HANDLE and NUS_TX_HANDLE in self.notify_callbacks:
            self.push_notification(NUS_TX_HANDLE, bytes(data))

    async def start_notify(self, ch, callback):
        self.notify_callbacks[ch.handle] = callback

    async def stop_notify(self, ch):
        self.notify_callbacks.pop(ch.handle, None)

    async def read_gatt_descriptor(self, handle):
        return bytearray(b"\x00\x00")

    async def write_gatt_descriptor(self, handle, data):
        pass

    # test helpers
    def push_notification(self, handle: int, data: bytes) -> None:
        ch = self.services.get_characteristic(handle)
        self.notify_callbacks[handle](ch, bytearray(data))

    def drop_link(self) -> None:
        self.is_connected = False
        self._dc(self)


class FakeScanner:
    """Mimics BleakScanner(detection_callback=...): start() replays ``adverts``
    (list of (BLEDevice, AdvertisementData)) on the event loop."""

    adverts: list = []
    fail: str | None = None   # start() raises this, like a missing/disabled adapter
    started = 0

    def __init__(self, detection_callback=None, scanning_mode="active", **kw) -> None:
        self._cb = detection_callback
        self.running = False

    async def start(self) -> None:
        if FakeScanner.fail:
            raise OSError(FakeScanner.fail)
        import asyncio
        FakeScanner.started += 1
        self.running = True
        loop = asyncio.get_running_loop()
        for i, (device, adv) in enumerate(FakeScanner.adverts):
            loop.call_later(0.01 * (i + 1), self._deliver, device, adv)

    def _deliver(self, device, adv) -> None:
        if self.running:
            self._cb(device, adv)

    async def stop(self) -> None:
        self.running = False


def advert(address: str, name: str | None = None, rssi: int = -60, mfr: dict | None = None,
           services: list | None = None, service_data: dict | None = None):
    from bleak.backends.device import BLEDevice
    from bleak.backends.scanner import AdvertisementData
    return (BLEDevice(address, name, None),
            AdvertisementData(name, mfr or {}, service_data or {}, services or [], None, rssi, ()))
