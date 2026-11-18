"""nRF Sniffer for Bluetooth LE: capture on-air packets to a pcap file (experimental)."""
from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..sniffer import pcap
from ..sniffer.model import CapturedPacket, PacketStore
from ..sniffer import nrf_sniffer
from ..sniffer.nrf_sniffer import DRAIN_MS, AirCapture, PortInfo
from ._logging import LogTap
from .errors import SnifferError

OPEN_TIMEOUT_S = 6.0   # the link probes two baud rates before giving up


@dataclass
class SniffResult:
    packets: int                      # on-air packets captured
    written: int                      # packets written to the pcap file
    path: str | None
    firmware: str | None
    seconds: float
    followed: str | None = None
    advertisers: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"packets": self.packets, "written": self.written, "pcap": self.path,
                "firmware": self.firmware, "seconds": round(self.seconds, 3),
                "followed": self.followed, "advertisers": self.advertisers}


def sniffer_ports() -> list[PortInfo]:
    """Serial ports, likely nRF Sniffers (Nordic/SEGGER USB ids) first (``.likely``)."""
    return nrf_sniffer.list_ports()


async def find_sniffer(serial_factory=None) -> str | None:
    """The likely port where an nRF Sniffer answers (it is pinged), or None."""
    # A plain thread, not asyncio.to_thread: the default executor breaks on Python 3.15.
    loop = asyncio.get_running_loop()
    done: asyncio.Future = loop.create_future()

    def work() -> None:
        try:
            r = nrf_sniffer.find_sniffer(None, serial_factory or nrf_sniffer.open_serial)
            loop.call_soon_threadsafe(lambda: done.done() or done.set_result(r))
        except BaseException as exc:      # noqa: BLE001 - handed to the awaiting task
            loop.call_soon_threadsafe(lambda: done.done() or done.set_exception(exc))
    threading.Thread(target=work, name="find-sniffer", daemon=True).start()
    return await done


async def sniff(port: str, duration: float, pcap_path: str | Path | None = None, *,
                follow: str | None = None, follow_timeout: float = 10.0,
                on_packet: Callable[[CapturedPacket], Any] | None = None,
                serial_factory=None) -> SniffResult:
    """Capture for ``duration`` seconds from the nRF Sniffer on ``port`` (e.g. "COM5").

    pcap_path  write on-air packets there as they arrive (link type 256, BLE LL with
               PHDR: Wireshark opens it directly); None captures without saving
    follow     address to follow into its connection; capture starts once the sniffer
               has seen it advertise (within ``follow_timeout`` s)
    on_packet  called for each decoded packet (``.info``, ``.protocol``, ``.raw``...)
    Raises SnifferError if no sniffer answers on the port.
    """
    store = PacketStore()
    air = AirCapture(store, serial_factory=serial_factory or nrf_sniffer.open_serial,
                     auto_drain=False)
    tap = LogTap(air.message, f"sniffer {port}")
    out = open(pcap_path, "wb") if pcap_path is not None else None
    written = total = last = 0
    loop = asyncio.get_running_loop()

    def pump() -> None:
        nonlocal written, total, last
        air.drain()
        for p in store.packets:
            if p.number <= last:
                continue
            last = p.number
            if p.air is None:
                continue
            total += 1
            if out is not None and len(p.raw) >= 9:
                out.write(pcap.record(p, store.epoch))
                written += 1
            if on_packet is not None:
                on_packet(p)

    async def run_until(cond: Callable[[], bool], seconds: float) -> bool:
        deadline = loop.time() + seconds
        while loop.time() < deadline:
            pump()
            if cond():
                return True
            await asyncio.sleep(DRAIN_MS / 1000)
        pump()
        return cond()

    t0 = time.monotonic()
    try:
        if out is not None:
            out.write(pcap.global_header())
        air.start(port)
        if not await run_until(lambda: air.state in ("scanning", "following", "idle"),
                               OPEN_TIMEOUT_S) or air.state == "idle":
            raise SnifferError(tap.error(f"No nRF Sniffer answered on {port}"))
        followed = None
        if follow is not None:
            target = follow.upper()
            if not await run_until(lambda: target in air.advertisers, follow_timeout):
                raise SnifferError(f"{target} was not seen advertising in {follow_timeout:g} s")
            air.follow(target)
            followed = target
        t0 = time.monotonic()
        await run_until(lambda: air.state == "idle", duration)
        if air.state == "idle":
            raise SnifferError(tap.error("The sniffer stopped responding"))
        advertisers = [{"address": a.address, "name": a.name, "rssi": a.rssi,
                        "packets": a.count, "random": a.random, "connectable": a.connectable}
                       for a in sorted(air.advertisers.values(), key=lambda a: -a.count)]
        return SniffResult(total, written, str(pcap_path) if pcap_path else None,
                           air.firmware, time.monotonic() - t0, followed, advertisers)
    finally:
        air.stop()
        if out is not None:
            out.close()
        tap.close()
