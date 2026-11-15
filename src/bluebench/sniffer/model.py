"""Captured packets and the store the Traffic tab displays."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, Signal

MAX_PACKETS = 200_000


@dataclass
class Field:
    """One node of the packet-details tree; ``span`` is a byte range in ``raw``."""
    label: str
    value: str = ""
    children: list["Field"] = field(default_factory=list)
    span: tuple[int, int] | None = None

    def add(self, label: str, value: str = "", span: tuple[int, int] | None = None) -> "Field":
        child = Field(label, value, span=span)
        self.children.append(child)
        return child


@dataclass
class AirMeta:
    """What the sniffer reported about an on-air packet (needed for pcap export)."""
    channel: int
    rssi: int
    flags: int              # nRF Sniffer flags byte
    timestamp_us: int       # sniffer clock, monotonic (wraparound removed)
    event_counter: int = 0
    coding_indicator: int | None = None   # LE Coded only (0 = S8, 1 = S2)

    @property
    def crc_ok(self) -> bool:
        return bool(self.flags & 0x01)

    @property
    def central_to_peripheral(self) -> bool:
        return bool(self.flags & 0x02)

    @property
    def encrypted(self) -> bool:
        return bool(self.flags & 0x04)

    @property
    def mic_ok(self) -> bool:
        return bool(self.flags & 0x08)

    @property
    def phy(self) -> int:  # 0 = 1M, 1 = 2M, 2 = Coded
        return (self.flags >> 4) & 0x07


@dataclass
class CapturedPacket:
    iface: str                  # "app" | "air"
    time: float                 # seconds since the capture started (host clock)
    source: str
    destination: str
    protocol: str
    info: str
    raw: bytes = b""            # bytes shown in the hex pane
    tree: list[Field] = field(default_factory=list)
    air: AirMeta | None = None
    error: bool = False
    empty: bool = False         # LL empty PDU (keep-alive)
    number: int = 0             # assigned by the store

    @property
    def length(self) -> int:
        return len(self.raw)

    def haystack(self) -> str:
        return f"{self.iface} {self.protocol} {self.source} {self.destination} {self.info}".lower()


class PacketStore(QObject):
    """All captured packets, shared by every capture source."""

    appended = Signal(int, int)   # first index, count
    reset = Signal()

    def __init__(self, parent: QObject | None = None, max_packets: int = MAX_PACKETS) -> None:
        super().__init__(parent)
        self.packets: list[CapturedPacket] = []
        self.max_packets = max_packets
        self._next_number = 1
        self.t0 = time.monotonic()      # packet times are relative to this
        self.epoch = time.time()        # wall clock at t0 (pcap timestamps)

    def now(self) -> float:
        return time.monotonic() - self.t0

    def add_many(self, packets: list[CapturedPacket]) -> None:
        if not packets:
            return
        for p in packets:
            p.number = self._next_number
            self._next_number += 1
        if len(self.packets) + len(packets) > self.max_packets:
            # Drop the oldest tenth at once rather than trimming every batch.
            keep = self.max_packets - len(packets) - self.max_packets // 10
            del self.packets[:max(0, len(self.packets) - max(keep, 0))]
            self.packets.extend(packets)
            self.reset.emit()
            return
        start = len(self.packets)
        self.packets.extend(packets)
        self.appended.emit(start, len(packets))

    def add(self, packet: CapturedPacket) -> None:
        self.add_many([packet])

    def clear(self) -> None:
        self.packets.clear()
        self._next_number = 1
        self.t0, self.epoch = time.monotonic(), time.time()
        self.reset.emit()
