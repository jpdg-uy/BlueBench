"""Driver for Nordic's "nRF Sniffer for Bluetooth LE" firmware over a serial port.

Works with an nRF52840 Dongle or an nRF52 DK flashed with the sniffer firmware
(the same hardware Wireshark's nRF Sniffer extcap plugin uses; close Wireshark
first, a COM port has a single owner).

Wire format (from Nordic's SnifferAPI and Wireshark's nordic_ble dissector):
SLIP frames (0xAB start, 0xBC end, 0xCD escape) carrying a 6-byte header
(length, protocol version, packet counter, packet id) and a payload. Packet
events carry a 10-byte radio header (flags, channel, RSSI, event counter,
timestamp) followed by the on-air packet: access address, PDU header,
payload and CRC, with one padding byte the firmware inserts after the header.

Serial I/O runs on a worker thread; everything Qt happens on the main thread
through a polling timer, so the UI never blocks on the port.
"""
from __future__ import annotations

import struct
import threading
import time
from collections import deque
from dataclasses import dataclass

from PySide6.QtCore import QObject, QTimer, Signal

from .ll_decode import ADV_ACCESS_ADDRESS, LLDecoder
from .model import AirMeta, CapturedPacket, PacketStore

SLIP_START, SLIP_END, SLIP_ESC = 0xAB, 0xBC, 0xCD
SLIP_ESC_START, SLIP_ESC_END, SLIP_ESC_ESC = 0xAC, 0xBD, 0xCE

REQ_FOLLOW = 0x00
EVENT_FOLLOW = 0x01
EVENT_PACKET_ADV_PDU = 0x02
EVENT_CONNECT = 0x05
EVENT_PACKET_DATA_PDU = 0x06
REQ_SCAN_CONT = 0x07
EVENT_DISCONNECT = 0x09
SET_TEMPORARY_KEY = 0x0C
PING_REQ = 0x0D
PING_RESP = 0x0E
REQ_VERSION = 0x1B
RESP_VERSION = 0x1C
GO_IDLE = 0xFE
PACKET_EVENTS = (EVENT_PACKET_ADV_PDU, EVENT_PACKET_DATA_PDU)

PROTOVER_V1 = 1
BAUD_RATES = (1_000_000, 460_800)
NORDIC_VIDS = (0x1915, 0x1366)   # Nordic Semiconductor, SEGGER (DK J-Link VCOM)
# nRF52840 Dongle waiting in its USB DFU bootloader (red LED pulsing): a Nordic port that
# is not a sniffer yet; the sniffer firmware has to be flashed first.
NORDIC_BOOTLOADER = (0x1915, 0x521F)
PROBE_TIMEOUT_S = 0.6
# With hardware flow control a write waits for CTS, forever on a port whose other end never
# raises it (the first serial port of the nRF52840 Dongle with the sniffer firmware 5).
WRITE_TIMEOUT_S = 1.0
DRAIN_MS = 50


class SnifferError(Exception):
    pass


# -- SLIP ------------------------------------------------------------------
def slip_encode(data: bytes) -> bytes:
    out = bytearray([SLIP_START])
    for b in data:
        if b == SLIP_START:
            out += bytes([SLIP_ESC, SLIP_ESC_START])
        elif b == SLIP_END:
            out += bytes([SLIP_ESC, SLIP_ESC_END])
        elif b == SLIP_ESC:
            out += bytes([SLIP_ESC, SLIP_ESC_ESC])
        else:
            out.append(b)
    out.append(SLIP_END)
    return bytes(out)


class SlipDecoder:
    """Incremental: feed arbitrary chunks, get complete frames back."""

    _UNESC = {SLIP_ESC_START: SLIP_START, SLIP_ESC_END: SLIP_END, SLIP_ESC_ESC: SLIP_ESC}

    def __init__(self) -> None:
        self._buf: bytearray | None = None   # None = waiting for a start byte
        self._esc = False

    def feed(self, data: bytes) -> list[bytes]:
        frames: list[bytes] = []
        for b in data:
            if b == SLIP_START:
                self._buf, self._esc = bytearray(), False   # (re)start, drops partial junk
            elif self._buf is None:
                continue
            elif self._esc:
                self._esc = False
                if b in self._UNESC:
                    self._buf.append(self._UNESC[b])
                else:
                    self._buf = None   # invalid escape: resynchronise
            elif b == SLIP_ESC:
                self._esc = True
            elif b == SLIP_END:
                frames.append(bytes(self._buf))
                self._buf = None
            else:
                self._buf.append(b)
        return frames


# -- frames ----------------------------------------------------------------
@dataclass
class SnifferFrame:
    protover: int
    counter: int
    id: int
    payload: bytes


def parse_frame(frame: bytes) -> SnifferFrame | None:
    if len(frame) < 6:
        return None
    protover = frame[2]
    if protover > 3:
        return None
    if protover <= 1:
        hlen, plen = frame[0], frame[1]
    else:
        hlen, plen = 6, struct.unpack_from("<H", frame, 0)[0]
    if hlen < 6:
        return None
    counter = struct.unpack_from("<H", frame, 3)[0]
    return SnifferFrame(protover, counter, frame[5], bytes(frame[hlen:hlen + plen]))


def build_command(packet_id: int, payload: bytes = b"", counter: int = 0) -> bytes:
    header = bytes([6, len(payload), PROTOVER_V1, counter & 0xFF, (counter >> 8) & 0xFF, packet_id])
    return slip_encode(header + payload)


def scan_payload(scan_responses: bool = True, aux: bool = True, coded: bool = False) -> bytes:
    return bytes([int(scan_responses) | int(aux) << 1 | int(coded) << 2])


def follow_payload(address: str, random: bool, only_adv: bool = False,
                   only_legacy: bool = False, coded: bool = False) -> bytes:
    addr = bytes.fromhex(address.replace(":", "").replace("-", ""))
    if len(addr) != 6:
        raise ValueError(f"Not a Bluetooth address: {address}")
    return addr + bytes([int(random), int(only_adv) | int(only_legacy) << 1 | int(coded) << 2])


class TimestampTracker:
    """Sniffer timestamps -> monotonic microseconds.

    Protocol v3 sends an absolute 32-bit µs clock that wraps; v2 sends the
    time since the end of the previous packet.
    """

    def __init__(self) -> None:
        self._last_raw: int | None = None
        self._wraps = 0
        self._mono = 0
        self._prev_duration = 0

    def update(self, protover: int, raw: int, packet_len: int, phy: int) -> int:
        if protover >= 3:
            if self._last_raw is not None and raw < self._last_raw:
                self._wraps += 1 << 32
            self._last_raw = raw
            self._mono = raw + self._wraps
        else:
            self._mono += raw + self._prev_duration
        # Air time of this packet (preamble + bytes), used by the v2 delta.
        us_per_byte = {0: 8, 1: 4, 2: 64}.get(phy, 8)
        self._prev_duration = (packet_len + (2 if phy == 1 else 1)) * us_per_byte
        return self._mono


def parse_packet_event(frame: SnifferFrame, clock: TimestampTracker) -> tuple[bytes, AirMeta] | None:
    """Packet event -> (on-air bytes AA+PDU+CRC, radio metadata)."""
    p = frame.payload
    if len(p) < 10:
        return None
    hlen = p[0] if 10 <= p[0] <= len(p) else 10
    flags, channel, rssi_raw = p[1], p[2], p[3]
    event_counter, ts = struct.unpack_from("<HI", p, 4)
    ble = bytearray(p[hlen:])
    phy = (flags >> 4) & 0x07
    ci = None
    if phy == 2:                       # AA, CI, header, padding, ...
        if len(ble) < 8:
            return None
        ci = ble[4] & 0x03
        del ble[7]
        del ble[4]
    else:                              # AA, header, padding, ...
        if len(ble) < 7:
            return None
        del ble[6]
    aa = struct.unpack_from("<I", ble, 0)[0]
    total = 6 + ble[5] + 3
    if aa != ADV_ACCESS_ADDRESS and ble[4] & 0x20:
        total += 1   # CTEInfo
    ll = bytes(ble[:total])
    mono = clock.update(frame.protover, ts, len(ll), phy)
    return ll, AirMeta(channel, -rssi_raw, flags, mono, event_counter, ci)


# -- ports -----------------------------------------------------------------
@dataclass
class PortInfo:
    device: str
    description: str
    likely: bool             # Nordic / SEGGER USB ids
    bootloader: bool = False  # Nordic DFU bootloader: no sniffer firmware on it

    @property
    def label(self) -> str:
        if self.bootloader:
            return f"{self.device} — {self.description}  (DFU bootloader: flash the nRF Sniffer firmware)"
        return f"{self.device} — {self.description}" + ("  ★" if self.likely else "")


def have_pyserial() -> bool:
    import importlib.util
    return importlib.util.find_spec("serial") is not None


def list_ports() -> list[PortInfo]:
    try:
        from serial.tools import list_ports as lp
    except ImportError:
        return []
    out = []
    for p in lp.comports():
        boot = (p.vid, p.pid) == NORDIC_BOOTLOADER
        out.append(PortInfo(p.device, p.description or "", p.vid in NORDIC_VIDS and not boot, boot))
    out.sort(key=lambda p: (not p.likely, p.device))
    return out


def open_serial(port: str, baud: int):
    try:
        import serial
    except ImportError:
        raise SnifferError("pyserial is not installed") from None
    try:
        return serial.Serial(port, baud, rtscts=True, timeout=0.05,
                             write_timeout=WRITE_TIMEOUT_S)
    except (serial.SerialException, OSError, ValueError) as exc:
        raise SnifferError(f"Cannot open {port}: {exc}") from None


# -- serial link (worker thread) ------------------------------------------
def probe(port: str, serial_factory=open_serial, stop: threading.Event | None = None):
    """Ping ``port`` at each baud rate: (open serial, baud) if an nRF Sniffer answers,
    else None. Raises SnifferError if the port cannot be opened."""
    for baud in BAUD_RATES:
        if stop is not None and stop.is_set():
            return None
        ser = serial_factory(port, baud)
        try:
            ser.reset_input_buffer()
        except Exception:
            pass
        try:
            ser.write(build_command(PING_REQ, b"", 0))
        except Exception:                 # write timeout: nobody reads this port
            ser.close()
            continue
        dec = SlipDecoder()
        deadline = time.monotonic() + PROBE_TIMEOUT_S
        while time.monotonic() < deadline and not (stop is not None and stop.is_set()):
            for raw in dec.feed(ser.read(256)):
                f = parse_frame(raw)
                if f is not None and f.id == PING_RESP:
                    return ser, baud
        ser.close()
    return None


def find_sniffer(ports: list[PortInfo] | None = None, serial_factory=open_serial) -> str | None:
    """The first likely port where an nRF Sniffer answers. The sniffer firmware 5 on the
    nRF52840 Dongle shows two serial ports with the same USB ids, and only one is the
    sniffer."""
    for p in (list_ports() if ports is None else ports):
        if not p.likely:
            continue
        try:
            found = probe(p.device, serial_factory)
        except SnifferError:
            continue
        if found is not None:
            found[0].close()
            return p.device
    return None


class SnifferLink:
    """Owns the port. Items for the main thread land in ``queue`` as
    ("opened", baud) | ("frame", SnifferFrame, host_monotonic) | ("error", text) | ("closed",).
    """

    def __init__(self, port: str, serial_factory=open_serial) -> None:
        self.port = port
        self._factory = serial_factory
        self._ser = None
        self._counter = 0
        self._stop = threading.Event()
        self._write_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.queue: deque = deque()
        self.baud: int | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="nrf-sniffer", daemon=True)
        self._thread.start()

    def send(self, packet_id: int, payload: bytes = b"") -> bool:
        with self._write_lock:
            if self._ser is None:
                return False
            data = build_command(packet_id, payload, self._counter)
            self._counter = (self._counter + 1) & 0xFFFF
            try:
                self._ser.write(data)
                return True
            except Exception as exc:
                self.queue.append(("error", f"Write to {self.port} failed: {exc}"))
                return False

    def close(self, wait: float = 1.0) -> None:
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(wait)

    # worker thread
    def _probe(self):
        found = probe(self.port, self._factory, self._stop)
        if found is not None:
            ser, self.baud = found
            return ser
        if self._stop.is_set():
            return None
        raise SnifferError(f"No nRF Sniffer answered on {self.port} (tried "
                           f"{', '.join(str(b) for b in BAUD_RATES)} baud). Is the nRF Sniffer "
                           "firmware flashed, and is Wireshark closed?")

    def _run(self) -> None:
        try:
            ser = self._probe()
        except SnifferError as exc:
            self.queue.append(("error", str(exc)))
            self.queue.append(("closed",))
            return
        if ser is None:
            self.queue.append(("closed",))
            return
        with self._write_lock:
            self._ser = ser
            self._counter = 1
        self.queue.append(("opened", self.baud))
        dec = SlipDecoder()
        try:
            while not self._stop.is_set():
                chunk = ser.read(max(1, getattr(ser, "in_waiting", 0) or 1))
                if not chunk:
                    continue
                now = time.monotonic()
                for raw in dec.feed(chunk):
                    f = parse_frame(raw)
                    if f is not None:
                        self.queue.append(("frame", f, now))
        except Exception as exc:
            if not self._stop.is_set():
                self.queue.append(("error", f"{self.port} stopped: {exc}"))
        finally:
            with self._write_lock:
                try:
                    ser.write(build_command(GO_IDLE, b"", self._counter))
                except Exception:
                    pass
                try:
                    ser.close()
                except Exception:
                    pass
                self._ser = None
            self.queue.append(("closed",))


# -- Qt side -----------------------------------------------------------------
@dataclass
class Advertiser:
    address: str
    random: bool
    name: str = ""
    rssi: int = 0
    count: int = 0
    connectable: bool = False

    @property
    def label(self) -> str:
        kind = "random" if self.random else "public"
        return f"{self.name or '(no name)'}  {self.address} ({kind}) {self.rssi} dBm"


@dataclass
class _Anchor:
    host: float | None = None
    sniffer_us: int = 0


class AirCapture(QObject):
    """Turns sniffer frames into CapturedPackets in a PacketStore."""

    state_changed = Signal(str)          # "idle" | "opening" | "scanning" | "following"
    message = Signal(str, str)           # level, text (for the log)
    advertisers_changed = Signal()

    def __init__(self, store: PacketStore, parent: QObject | None = None,
                 serial_factory=open_serial, auto_drain: bool = True) -> None:
        """``auto_drain=False`` skips the Qt timer (it needs a Qt event loop); the
        owner then calls ``drain()`` itself, as the headless API does."""
        super().__init__(parent)
        self.store = store
        self.state = "idle"
        self.decoder = LLDecoder()
        self.advertisers: dict[str, Advertiser] = {}
        self.following: str | None = None
        self.firmware: str | None = None
        self._factory = serial_factory
        self._link: SnifferLink | None = None
        self._clock = TimestampTracker()
        self._anchor = _Anchor()
        self._adv_dirty = False
        self._timer: QTimer | None = None
        if auto_drain:
            self._timer = QTimer(self)
            self._timer.setInterval(DRAIN_MS)
            self._timer.timeout.connect(self.drain)

    @property
    def running(self) -> bool:
        return self.state != "idle"

    def _set_state(self, state: str) -> None:
        if state != self.state:
            self.state = state
            self.state_changed.emit(state)

    def start(self, port: str) -> None:
        if self._link is not None:
            return
        self.decoder = LLDecoder()
        self._clock = TimestampTracker()
        self._anchor = _Anchor()
        self.following = None
        self._link = SnifferLink(port, self._factory)
        self._set_state("opening")
        self.message.emit("info", f"Opening nRF Sniffer on {port}…")
        self._link.start()
        if self._timer is not None:
            self._timer.start()

    def stop(self) -> None:
        if self._link is not None:
            self._link.close()
            self.drain()
        if self._timer is not None:
            self._timer.stop()
        self._link = None
        self._set_state("idle")

    def scan(self) -> None:
        if self._link is None:
            return
        self._link.send(REQ_SCAN_CONT, scan_payload())
        self._link.send(SET_TEMPORARY_KEY, bytes(16))   # lets it decrypt legacy Just Works
        self.following = None
        if self.state != "opening":
            self._set_state("scanning")

    def follow(self, address: str) -> None:
        adv = self.advertisers.get(address)
        if self._link is None or adv is None:
            return
        self._link.send(REQ_FOLLOW, follow_payload(address, adv.random))
        self.following = address
        self._set_state("following")
        self.message.emit("info", f"Sniffer following {adv.name or address}; connect to it now "
                                  "(from this app or any central) to capture the connection")

    def drain(self) -> None:
        link = self._link
        if link is None:
            return
        batch: list[CapturedPacket] = []
        q = link.queue
        while q:
            item = q.popleft()
            kind = item[0]
            if kind == "frame":
                pkt = self._on_frame(item[1], item[2])
                if pkt is not None:
                    batch.append(pkt)
            elif kind == "opened":
                self.message.emit("info", f"nRF Sniffer ready on {link.port} at {item[1]} baud")
                link.send(REQ_VERSION)
                self._set_state("scanning")
                self.scan()
            elif kind == "error":
                self.message.emit("error", item[1])
            elif kind == "closed":
                if self._timer is not None:
                    self._timer.stop()
                self._link = None
                self._set_state("idle")
        self.store.add_many(batch)
        if self._adv_dirty:
            self._adv_dirty = False
            self.advertisers_changed.emit()

    def _on_frame(self, frame: SnifferFrame, host_time: float) -> CapturedPacket | None:
        if frame.id in PACKET_EVENTS:
            parsed = parse_packet_event(frame, self._clock)
            if parsed is None:
                return None
            ll, meta = parsed
            if self._anchor.host is None:
                self._anchor = _Anchor(host_time, meta.timestamp_us)
            t = self._anchor.host + (meta.timestamp_us - self._anchor.sniffer_us) / 1e6 - self.store.t0
            pkt = self.decoder.decode(ll, meta, t)
            self._note_advertiser(ll, meta)
            return pkt
        if frame.id == RESP_VERSION:
            self.firmware = frame.payload.split(b"\0", 1)[0].decode("ascii", "replace")
            self.message.emit("info", f"nRF Sniffer firmware {self.firmware}")
        elif frame.id == EVENT_CONNECT:
            self.message.emit("info", "Sniffer: connection established")
        elif frame.id == EVENT_DISCONNECT:
            self.message.emit("info", "Sniffer: connection ended")
        return None

    def _note_advertiser(self, ll: bytes, meta: AirMeta) -> None:
        if len(ll) < 12 or struct.unpack_from("<I", ll)[0] != ADV_ACCESS_ADDRESS or not meta.crc_ok:
            return
        ptype = ll[4] & 0x0F
        if ptype not in (0, 1, 2, 4, 6):
            return
        address = ":".join(f"{b:02X}" for b in reversed(ll[6:12]))
        adv = self.advertisers.get(address)
        if adv is None:
            adv = self.advertisers[address] = Advertiser(address, bool(ll[4] & 0x40))
            self._adv_dirty = True
        adv.rssi, adv.count = meta.rssi, adv.count + 1
        adv.connectable = adv.connectable or ptype in (0, 1)
        name = self.decoder.names.get(address, "")
        if name and name != adv.name:
            adv.name = name
            self._adv_dirty = True
