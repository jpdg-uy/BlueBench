"""BleakScanner wrapper that keeps one entry per device and exposes Qt signals.

A device that changes its private address (a resolvable private address, RPA, is
typically renewed every few minutes, some firmware does it every few seconds)
stays one entry: the entry keeps the key of its first address and follows the
newest one. Two ways to tell it is the same device:

* identity: a registered decoder found something stable in the advertisement,
  such as a serial number sent in clear (exact);
* rotation guess: a new private address shows up just as one with the same
  name, the same kind of data and a similar signal goes quiet. If the old address
  is heard again, both were real devices and the guess is undone.
"""
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
# A new private address may continue a device that went quiet up to this long ago...
ROTATION_WINDOW_S = 30.0
# ... and whose signal was this close to the new one.
ROTATION_RSSI_DB = 15
PRIVATE_KINDS = ("rpa", "nrpa")
LATE_PACKET_S = 0.5   # an old address heard this soon after its last packet is a straggler
BOTH_ALIVE_S = 2.0    # old and new address both heard within this: two devices


def address_kind(address: str, address_type: str | None) -> str | None:
    """"public", "static", "rpa" (resolvable private), "nrpa" (non-resolvable) or
    "random"; None when the platform does not say whether the address is random.

    A random address tells its kind in its two most significant bits.
    """
    if address_type == "public":
        return "public"
    if address_type != "random":
        return None
    try:
        top = int(address.replace("-", ":").split(":")[0], 16) >> 6
    except ValueError:
        return "random"
    return {0b11: "static", 0b01: "rpa", 0b00: "nrpa"}.get(top, "random")


KIND_TEXT = {"public": "public", "static": "random static", "rpa": "resolvable private (RPA)",
             "nrpa": "non-resolvable private", "random": "random"}


def address_type_of(device: BLEDevice) -> str | None:
    """"public" or "random" from the backend details, None if unknown."""
    details = getattr(device, "details", None)
    if isinstance(details, dict):  # BlueZ
        t = (details.get("props") or {}).get("AddressType")
        return t if t in ("public", "random") else None
    for args in (getattr(details, "adv", None), getattr(details, "scan", None)):  # WinRT
        t = getattr(args, "bluetooth_address_type", None)
        if t is None:
            continue
        name = str(getattr(t, "name", t)).lower()
        if "public" in name:
            return "public"
        if "random" in name:
            return "random"
    return None


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
    key: str = ""                      # row key: the first address seen, never changes
    address_kind: str | None = None    # see address_kind()
    # Every address this device used, oldest first, with when it was last heard.
    addresses: dict[str, float] = field(default_factory=dict)
    first_heard: dict[str, float] = field(default_factory=dict)
    joined: str | None = None          # how addresses were joined: "identity" or "guess"

    def __post_init__(self) -> None:
        self.key = self.key or self.address
        self.addresses.setdefault(self.address, self.last_seen)
        self.first_heard.setdefault(self.address, self.first_seen)

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

    device_updated = Signal(str)  # the entry's key
    scanning_changed = Signal(bool)
    error = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.devices: dict[str, DeviceEntry] = {}  # key -> entry
        self._scanner: BleakScanner | None = None
        self._key_of: dict[str, str] = {}          # any address seen -> entry key
        self._by_identity: dict[tuple, str] = {}
        self.follow_rotation = True                # join rotating private addresses

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
        self._key_of.clear()
        self._by_identity.clear()

    def entry_for(self, address_or_key: str) -> DeviceEntry | None:
        """The entry for a key or for any address the device used."""
        e = self.devices.get(address_or_key)
        if e is None:
            key = self._key_of.get(address_or_key) or self._key_of.get(address_or_key.upper())
            e = self.devices.get(key) if key else None
        return e

    def _on_detection(self, device: BLEDevice, data: AdvertisementData) -> None:
        now = time.monotonic()
        parsed = parse_advertisement(
            data.local_name,
            dict(data.manufacturer_data),
            dict(data.service_data),
            list(data.service_uuids),
            data.tx_power,
        )
        addr = device.address
        name = data.local_name or device.name
        entry = self.entry_for(addr)
        if entry is not None and entry.joined == "guess" and addr != entry.address:
            entry = self._undo_guess(entry, addr, now)
        if entry is None:
            kind = address_kind(addr, address_type_of(device))
            entry = self._continued(parsed, name, kind, data.rssi, now)
            if entry is None:
                entry = DeviceEntry(addr, name, data.rssi, parsed, device,
                                    first_seen=now, last_seen=now)
                entry.address_kind = kind
                self.devices[entry.key] = entry
            self._key_of[addr] = entry.key
        else:
            # Scan responses often arrive separately and carry only part of the
            # payload, so keep the richest name we have seen.
            entry.name = data.local_name or entry.name or device.name
            entry.adv = parsed if parsed.fields else entry.adv
            entry.adv_count += 1
        if parsed.identity is not None:
            self._by_identity.setdefault(parsed.identity, entry.key)
        entry.address = addr
        entry.addresses[addr] = now
        entry.first_heard.setdefault(addr, now)
        entry.rssi = data.rssi
        entry.device = device
        entry.last_seen = now
        entry.rssi_history.append((now, data.rssi))
        self.device_updated.emit(entry.key)

    def _continued(self, adv: ParsedAdvertisement, name: str | None, kind: str | None,
                   rssi: int, now: float) -> DeviceEntry | None:
        """The entry a new address belongs to, if it is a known device with a new address."""
        if adv.identity is not None:
            e = self.devices.get(self._by_identity.get(adv.identity, ""))
            if e is not None:
                e.joined = "identity"
                self._join(e, adv, name, kind)
            return e
        if not self.follow_rotation or kind not in PRIVATE_KINDS:
            return None
        best: DeviceEntry | None = None
        for e in self.devices.values():
            if (e.address_kind != kind or e.joined == "identity"
                    or now - e.last_seen > ROTATION_WINDOW_S
                    or abs(e.smoothed_rssi(now=now) - rssi) > ROTATION_RSSI_DB
                    or not _alike(e, adv, name)):
                continue
            if best is None or e.last_seen > best.last_seen:
                best = e
        if best is not None:
            best.joined = "guess"
            self._join(best, adv, name, kind)
        return best

    def _join(self, e: DeviceEntry, adv: ParsedAdvertisement, name: str | None,
              kind: str | None) -> None:
        e.name = name or e.name
        e.adv = adv if adv.fields else e.adv
        e.address_kind = kind or e.address_kind
        e.adv_count += 1

    def _undo_guess(self, entry: DeviceEntry, addr: str, now: float) -> DeviceEntry:
        """An older address of a guessed entry is heard again while a newer one is in
        use: they are two devices. The newer addresses move to an entry of their own."""
        order = list(entry.addresses)  # oldest first
        newer = order[order.index(addr) + 1:] if addr in order else []
        # A straggler right after the switch is not a second device, nor is an old
        # address coming back once the newer one has gone quiet.
        if (not newer or now - entry.first_heard.get(newer[0], now) < LATE_PACKET_S
                or now - max(entry.addresses[a] for a in newer) > BOTH_ALIVE_S):
            return entry
        moved = DeviceEntry(newer[0], entry.name, entry.rssi, entry.adv, entry.device,
                            first_seen=entry.addresses[newer[0]], last_seen=entry.last_seen)
        moved.address_kind = entry.address_kind
        moved.addresses = {a: entry.addresses.pop(a) for a in newer}
        moved.first_heard = {a: entry.first_heard.pop(a, now) for a in newer}
        moved.address = newer[-1]
        self.devices[moved.key] = moved
        for a in newer:
            self._key_of[a] = moved.key
        if len(entry.addresses) == 1:
            entry.joined = None
        self.device_updated.emit(moved.key)
        return entry


def _alike(e: DeviceEntry, adv: ParsedAdvertisement, name: str | None) -> bool:
    """Whether a new address's first advertisement looks like the device in ``e``."""
    if name and e.name and name != e.name:
        return False
    if adv.similarity is not None or e.adv.similarity is not None:
        return adv.similarity == e.adv.similarity
    if adv.manufacturer_data and e.adv.manufacturer_data:
        def shape(m: dict[int, bytes]) -> set[tuple[int, int]]:
            return {(cid, len(p)) for cid, p in m.items()}
        if shape(adv.manufacturer_data) != shape(e.adv.manufacturer_data):
            return False
    if adv.service_uuids and e.adv.service_uuids and set(adv.service_uuids) != set(e.adv.service_uuids):
        return False
    # Nothing at all to compare: too weak a hint to join two addresses.
    return bool(name and e.name) or bool(adv.manufacturer_data) or bool(adv.service_uuids)
