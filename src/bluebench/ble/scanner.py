"""BleakScanner wrapper that keeps one entry per device and exposes Qt signals."""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from itertools import takewhile

from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from PySide6.QtCore import QObject, Signal

from .adv_parser import ParsedAdvertisement, parse_advertisement

RSSI_HISTORY = 120
# RSSI jumps several dB between advertisements; sorting and filtering use the
# average over this window so rows do not reshuffle on every packet.
RSSI_SMOOTH_S = 2.5


@dataclass
class DeviceEntry:
    address: str
    name: str | None
    rssi: int
    adv: ParsedAdvertisement
    device: BLEDevice
    first_seen: float = field(default_factory=time.monotonic)
    last_seen: float = field(default_factory=time.monotonic)
    adv_count: int = 1
    rssi_history: deque[tuple[float, int]] = field(
        default_factory=lambda: deque(maxlen=RSSI_HISTORY)
    )

    @property
    def display_name(self) -> str:
        return self.name or "N/A"

    def smoothed_rssi(self, window_s: float = RSSI_SMOOTH_S, now: float | None = None) -> int:
        """Average RSSI of the advertisements seen in the last ``window_s`` seconds.

        Falls back to the latest value when nothing arrived in the window (a device
        that went quiet keeps its last reading).
        """
        now = time.monotonic() if now is None else now
        recent = [r for _, r in takewhile(lambda s: now - s[0] <= window_s,
                                          reversed(self.rssi_history))]
        return round(sum(recent) / len(recent)) if recent else self.rssi

    def interval_ms(self) -> float | None:
        """Rough average advertising interval seen by this scanner."""
        if self.adv_count < 2:
            return None
        return (self.last_seen - self.first_seen) * 1000 / (self.adv_count - 1)


class Scanner(QObject):
    """Emits ``device_updated(address)`` for every advertisement received.

    bleak delivers detection callbacks on the asyncio loop, which qasync runs
    on the Qt main thread, so emitting signals from the callback is safe.
    """

    device_updated = Signal(str)
    scanning_changed = Signal(bool)
    error = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.devices: dict[str, DeviceEntry] = {}
        self._scanner: BleakScanner | None = None

    @property
    def is_scanning(self) -> bool:
        return self._scanner is not None

    async def start(self) -> None:
        if self._scanner:
            return
        scanner = BleakScanner(detection_callback=self._on_detection, scanning_mode="active")
        try:
            await scanner.start()
        except Exception as exc:  # bleak raises backend-specific errors
            self.error.emit(f"Could not start scanning: {exc}")
            return
        self._scanner = scanner
        self.scanning_changed.emit(True)

    async def stop(self) -> None:
        scanner, self._scanner = self._scanner, None
        if scanner is None:
            return
        try:
            await scanner.stop()
        except Exception as exc:
            self.error.emit(f"Error stopping scan: {exc}")
        self.scanning_changed.emit(False)

    def clear(self) -> None:
        self.devices.clear()

    def _on_detection(self, device: BLEDevice, data: AdvertisementData) -> None:
        now = time.monotonic()
        parsed = parse_advertisement(
            data.local_name,
            dict(data.manufacturer_data),
            dict(data.service_data),
            list(data.service_uuids),
            data.tx_power,
        )
        entry = self.devices.get(device.address)
        if entry is None:
            entry = DeviceEntry(device.address, data.local_name or device.name, data.rssi,
                                parsed, device, first_seen=now, last_seen=now)
            self.devices[device.address] = entry
        else:
            # Scan responses often arrive separately and carry only part of the
            # payload, so keep the richest name we have seen.
            entry.name = data.local_name or entry.name or device.name
            entry.rssi = data.rssi
            entry.adv = parsed if parsed.fields else entry.adv
            entry.device = device
            entry.last_seen = now
            entry.adv_count += 1
        entry.rssi_history.append((now, data.rssi))
        self.device_updated.emit(device.address)
