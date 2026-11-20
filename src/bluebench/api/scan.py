"""Scanning: list advertising devices, stream them as they appear, or find one."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import Any

from ..ble import uuids
from ..ble.adv_filter import DeviceFilter, split_criteria
from ..ble.adv_parser import manufacturer_raw
from ..ble.scanner import DeviceEntry, Scanner
from .errors import BluetoothUnavailable, DeviceNotFound


@dataclass
class ScanResult:
    """One advertising device, with its advertisement data merged across packets
    (advertising + scan responses)."""

    address: str
    name: str | None
    rssi: int
    manufacturer_data: dict[int, bytes] = field(default_factory=dict)
    service_uuids: list[str] = field(default_factory=list)
    service_data: dict[str, bytes] = field(default_factory=dict)
    tx_power: int | None = None
    beacon: str | None = None
    packets: int = 1
    device: Any = field(default=None, repr=False, compare=False)  # bleak BLEDevice

    @classmethod
    def from_entry(cls, e: DeviceEntry) -> "ScanResult":
        return cls(e.address, e.name, e.rssi, dict(e.adv.manufacturer_data),
                   list(e.adv.service_uuids), dict(e.adv.service_data), e.adv.tx_power,
                   e.adv.beacon, e.adv_count, e.device)

    def to_dict(self) -> dict:
        """JSON-ready form: bytes as lowercase hex, company IDs as 0xNNNN."""
        return {
            "address": self.address,
            "name": self.name,
            "rssi": self.rssi,
            "manufacturer_data": [
                {"company_id": f"0x{cid:04X}", "company": uuids.company_name(cid),
                 "data": payload.hex(), "raw": manufacturer_raw(cid, payload).hex()}
                for cid, payload in self.manufacturer_data.items()],
            "service_uuids": [uuids.short_form(u) for u in self.service_uuids],
            "service_data": {uuids.short_form(u): d.hex() for u, d in self.service_data.items()},
            "tx_power": self.tx_power,
            "beacon": self.beacon,
            "packets": self.packets,
        }


def make_filter(flt: DeviceFilter | None = None, **criteria) -> DeviceFilter:
    """A DeviceFilter from keyword criteria (name, address, mfr, service, min_rssi,
    exact_name), or the one given. Raises ValueError for a bad ``mfr``."""
    if flt is not None:
        if criteria:
            raise TypeError("pass either a DeviceFilter or keyword criteria, not both")
        return flt
    return DeviceFilter(**split_criteria(criteria))


def _matches(flt: DeviceFilter, e: DeviceEntry) -> bool:
    return flt.matches(address=e.address, name=e.name, rssi=e.rssi,
                       manufacturer_data=e.adv.manufacturer_data,
                       service_uuids=e.adv.service_uuids, adv=e.adv)


class _Scan:
    """Runs the shared Scanner (same merging as the GUI) and raises instead of
    emitting an error signal when Bluetooth is unavailable."""

    def __init__(self) -> None:
        self.scanner = Scanner()
        self.updates: asyncio.Queue[str] = asyncio.Queue()
        self._errors: list[str] = []
        self.scanner.device_updated.connect(self.updates.put_nowait)
        self.scanner.error.connect(self._errors.append)

    async def __aenter__(self) -> "_Scan":
        await self.scanner.start()
        if not self.scanner.is_scanning:
            raise BluetoothUnavailable(self._errors[-1] if self._errors
                                       else "Could not start scanning")
        return self

    async def __aexit__(self, *exc) -> None:
        await self.scanner.stop()


async def scan(timeout: float = 5.0, flt: DeviceFilter | None = None,
               **criteria) -> list[ScanResult]:
    """Scan for ``timeout`` seconds; matching devices, strongest signal first.

    Criteria: name (substring), address, mfr (hex prefix such as "5900"),
    service (UUID), min_rssi, exact_name. Or pass a DeviceFilter.
    """
    f = make_filter(flt, **criteria)
    async with _Scan() as s:
        await asyncio.sleep(timeout)
        entries = list(s.scanner.devices.values())
    found = [ScanResult.from_entry(e) for e in entries if _matches(f, e)]
    return sorted(found, key=lambda r: r.rssi, reverse=True)


async def scan_stream(timeout: float | None = None, flt: DeviceFilter | None = None, *,
                      updates: bool = False, **criteria) -> AsyncIterator[ScanResult]:
    """Yield matching devices as they are seen, for ``timeout`` seconds (None: until
    the caller stops iterating). ``updates=True`` yields every advertisement, not
    just the first one per device.

    Leaving the loop early stops the scan when the generator is closed; wrap it in
    ``contextlib.aclosing()`` to make that immediate.
    """
    f = make_filter(flt, **criteria)
    loop = asyncio.get_running_loop()
    deadline = None if timeout is None else loop.time() + timeout
    seen: set[str] = set()
    async with _Scan() as s:
        while True:
            remaining = None if deadline is None else deadline - loop.time()
            if remaining is not None and remaining <= 0:
                return
            try:
                address = await asyncio.wait_for(s.updates.get(), remaining)
            except asyncio.TimeoutError:
                return
            e = s.scanner.devices.get(address)
            if e is None or not _matches(f, e) or (address in seen and not updates):
                continue
            seen.add(address)
            yield ScanResult.from_entry(e)


async def find(timeout: float = 10.0, flt: DeviceFilter | None = None,
               **criteria) -> ScanResult:
    """The first device matching the criteria; stops scanning as soon as it appears.
    Raises DeviceNotFound after ``timeout`` seconds."""
    f = make_filter(flt, **criteria)
    async with aclosing(scan_stream(timeout, f)) as stream:
        async for result in stream:
            return result
    raise DeviceNotFound(f"No device matching {describe(f)} found in {timeout:g} s")


def describe(f: DeviceFilter) -> str:
    parts = [f"{k}={v!r}" for k, v in (("name", f.name), ("address", f.address),
                                         ("mfr", f.mfr), ("service", f.service),
                                         ("min_rssi", f.min_rssi)) if v is not None]
    parts += [f"{k}={v!r}" for k, v in f.fields]
    return ", ".join(parts) or "any criteria"
