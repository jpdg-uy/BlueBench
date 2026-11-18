"""Traffic tab back end: nRF Sniffer protocol, LL/ATT/SMP decoding, pcap, captures."""
from __future__ import annotations

import struct
import threading
import time
from collections import deque
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Signal

from bluebench.sniffer import nrf_sniffer as ns
from bluebench.sniffer import pcap
from bluebench.sniffer.app_capture import AppCapture
from bluebench.sniffer.ll_decode import ADV_ACCESS_ADDRESS, LLDecoder
from bluebench.sniffer.model import AirMeta, PacketStore
from fake_air import (ADV_AA, C2P, CENTRAL, CRC, P2C, PERIPH, adv_ind, connect_ind,
                      data_pdu, l2cap, mac, meta, sniffer_frame)

def all_fields(fields):
    for f in fields:
        yield f
        yield from all_fields(f.children)


def labels(pkt) -> dict[str, str]:
    return {f.label: f.value for f in all_fields(pkt.tree)}


# -- SLIP / frames ---------------------------------------------------------------
def test_slip_round_trip_escapes_and_resyncs():
    data = bytes([1, 0xAB, 2, 0xBC, 3, 0xCD, 4])
    enc = ns.slip_encode(data)
    assert enc[0] == 0xAB and enc[-1] == 0xBC
    assert 0xAB not in enc[1:-1] and 0xBC not in enc[1:-1]
    dec = ns.SlipDecoder()
    stream = b"\x00junk" + enc + enc
    frames = []
    for i in range(0, len(stream), 3):    # arbitrary chunking
        frames += dec.feed(stream[i:i + 3])
    assert frames == [data, data]


def test_build_command_is_v1_frame():
    raw = ns.SlipDecoder().feed(ns.build_command(ns.REQ_FOLLOW, b"\x01\x02", counter=0x1234))[0]
    assert raw == bytes([6, 2, 1, 0x34, 0x12, ns.REQ_FOLLOW, 1, 2])
    f = ns.parse_frame(raw)
    assert (f.protover, f.counter, f.id, f.payload) == (1, 0x1234, ns.REQ_FOLLOW, b"\x01\x02")


def test_parse_frame_rejects_unknown_versions():
    assert ns.parse_frame(b"\x00\x00\x04\x00\x00\x02") is None
    assert ns.parse_frame(b"\x01\x02") is None


def test_follow_payload_keeps_display_order():
    assert ns.follow_payload("C4:12:34:56:78:9A", True) == bytes.fromhex("C4123456789A") + b"\x01\x00"
    with pytest.raises(ValueError):
        ns.follow_payload("C4:12", False)


def test_packet_event_removes_padding_and_reads_radio_header():
    ll = adv_ind(PERIPH, b"\x02\x01\x06")
    f = ns.parse_frame(sniffer_frame(ll, flags=0x01 | 0x10, channel=38, rssi=-71, ts=5000))
    out, m = ns.parse_packet_event(f, ns.TimestampTracker())
    assert out == ll
    assert (m.channel, m.rssi, m.crc_ok, m.phy, m.timestamp_us) == (38, -71, True, 1, 5000)


def test_packet_event_coded_phy_keeps_coding_indicator():
    ll = adv_ind(PERIPH, b"")
    f = ns.parse_frame(sniffer_frame(ll, flags=0x01 | 0x20, coded_ci=1))
    out, m = ns.parse_packet_event(f, ns.TimestampTracker())
    assert out == ll and m.phy == 2 and m.coding_indicator == 1


def test_timestamps_unwrap_v3_and_accumulate_v2():
    t = ns.TimestampTracker()
    assert t.update(3, 0xFFFFFF00, 20, 0) == 0xFFFFFF00
    assert t.update(3, 0x10, 20, 0) == 0x1_0000_0010
    t2 = ns.TimestampTracker()
    a = t2.update(2, 100, 10, 0)
    b = t2.update(2, 150, 10, 0)
    assert a == 100 and b == 100 + 150 + (10 + 1) * 8


# -- decoding --------------------------------------------------------------------
def test_adv_ind_with_name_and_flags():
    ad = b"\x02\x01\x06" + b"\x08\x09Thingy!" + b"\x03\x03\x0d\x18"
    pkt = LLDecoder().decode(adv_ind(PERIPH, ad), meta(), 1.0)
    assert pkt.source == PERIPH and pkt.destination == "Broadcast"
    assert pkt.info == 'ADV_IND "Thingy!"'
    f = labels(pkt)
    assert f["Flags"] == "LE General Discoverable, BR/EDR Not Supported"
    assert f["Complete Local Name"] == "Thingy!"
    assert f["AdvA"] == PERIPH
    for field in all_fields(pkt.tree):     # every span lies inside the packet
        if field.span:
            assert 0 <= field.span[0] < field.span[1] <= len(pkt.raw)


def test_scan_req_and_bad_crc():
    payload = mac(CENTRAL) + mac(PERIPH)
    ll = ADV_AA + bytes([0x03, len(payload)]) + payload + CRC
    pkt = LLDecoder().decode(ll, AirMeta(37, -40, 0x00, 0), 0.0)
    assert (pkt.source, pkt.destination) == (CENTRAL, PERIPH)
    assert pkt.error and "CRC error" in pkt.info


def connected_decoder() -> LLDecoder:
    d = LLDecoder()
    pkt = d.decode(connect_ind(CENTRAL, PERIPH), meta(), 0.0)
    assert pkt.info.startswith("CONNECT_IND, interval 30 ms")
    assert labels(pkt)["Supervision Timeout"] == "4000 ms"
    return d


def test_data_direction_and_empty_pdu():
    d = connected_decoder()
    pkt = d.decode(data_pdu(1, b""), meta(C2P), 0.1)
    assert pkt.empty and pkt.info == "Empty PDU"
    assert (pkt.source, pkt.destination) == (CENTRAL, PERIPH)
    pkt = d.decode(data_pdu(1, b""), meta(P2C), 0.1)
    assert (pkt.source, pkt.destination) == (PERIPH, CENTRAL)


def test_ll_control_version_and_terminate():
    d = connected_decoder()
    pkt = d.decode(data_pdu(3, bytes([0x0C, 12]) + struct.pack("<HH", 0x0059, 0x1234)), meta(P2C), 0)
    assert pkt.info.startswith("LL_VERSION_IND (Bluetooth 5.3, Nordic")
    pkt = d.decode(data_pdu(3, bytes([0x02, 0x13])), meta(C2P), 0)
    assert pkt.info == "LL_TERMINATE_IND (Remote User Terminated Connection)"


def test_att_learns_handles_from_discovery():
    d = connected_decoder()
    # Central asks for characteristic declarations, peripheral answers.
    req = bytes([0x08]) + struct.pack("<HH", 1, 0xFFFF) + b"\x03\x28"
    d.decode(data_pdu(2, l2cap(4, req)), meta(C2P), 0)
    decl = struct.pack("<H", 0x000E) + bytes([0x10]) + struct.pack("<H", 0x000F) + b"\x37\x2a"
    rsp = d.decode(data_pdu(2, l2cap(4, bytes([0x09, 7]) + decl)), meta(P2C), 0)
    assert rsp.protocol == "ATT" and rsp.info == "Rcvd Read By Type Response (1 characteristic)"
    grp = d.decode(data_pdu(2, l2cap(4, b"\x10\x01\x00\xff\xff\x00\x28")), meta(C2P), 0)
    assert grp.info.endswith("Primary Service (0x2800)")
    assert any("0x000F: Heart Rate Measurement" in f.value for f in all_fields(rsp.tree))
    note = d.decode(data_pdu(2, l2cap(4, b"\x1b\x0f\x00\x06\x48")), meta(P2C), 0)
    assert note.info == "Rcvd Handle Value Notification, Handle: 0x000F " \
                        "(Heart Rate Measurement (0x2A37)), Value: 06 48"
    write = d.decode(data_pdu(2, l2cap(4, b"\x12\x10\x00\x01\x00")), meta(C2P), 0)
    assert write.info.startswith("Sent Write Request, Handle: 0x0010")


def test_att_error_response_is_flagged():
    d = connected_decoder()
    pkt = d.decode(data_pdu(2, l2cap(4, b"\x01\x0a\x12\x00\x05")), meta(P2C), 0)
    assert pkt.error and "Insufficient Authentication" in pkt.info


def test_l2cap_reassembly():
    d = connected_decoder()
    frame = l2cap(4, b"\x1b\x0f\x00" + bytes(range(30)))
    first = d.decode(data_pdu(2, frame[:20]), meta(P2C), 0)
    assert first.info.startswith("L2CAP fragment (start, 20/37")
    last = d.decode(data_pdu(1, frame[20:]), meta(P2C), 0)
    assert last.protocol == "ATT" and "Handle Value Notification" in last.info
    assert "Reassembled L2CAP" in labels(last)


def test_smp_pairing_request():
    d = connected_decoder()
    pkt = d.decode(data_pdu(2, l2cap(6, bytes([0x01, 0x04, 0x00, 0x0D, 16, 0x07, 0x07]))), meta(C2P), 0)
    assert pkt.protocol == "SMP"
    assert pkt.info == "Pairing Request: KeyboardDisplay, SC, MITM, Bonding"


def test_encrypted_without_key_and_decrypted_with_mic():
    d = connected_decoder()
    pkt = d.decode(data_pdu(2, bytes(12)), meta(P2C | 0x04), 0)
    assert pkt.info == "Encrypted packet (12 bytes)"
    plain = l2cap(4, b"\x0b\x41") + b"MIC!"
    pkt = d.decode(data_pdu(2, plain), meta(P2C | 0x04 | 0x08), 0)
    assert pkt.protocol == "ATT" and pkt.info.endswith("Value: 41")


def test_truncated_packets_do_not_raise():
    d = connected_decoder()
    for ll in (ADV_AA + b"\x00", data_pdu(2, b"\x05\x00\x04"), data_pdu(3, b"\x0c"),
               ADV_AA + bytes([0x05, 3]) + b"\x01\x02\x03" + CRC):
        d.decode(ll, meta(), 0)


# -- pcap ------------------------------------------------------------------------
def test_pcap_records(tmp_path):
    d = connected_decoder()
    adv = d.decode(adv_ind(PERIPH, b"\x02\x01\x06"), AirMeta(39, -70, 0x01 | 0x10, 0), 0.5)
    data = d.decode(data_pdu(1, b""), AirMeta(7, -50, C2P, 0), 1.25)
    path = tmp_path / "c.pcap"
    assert pcap.write(path, [adv, data], epoch=1_700_000_000.0) == 2
    raw = path.read_bytes()
    magic, vmaj, vmin, _, _, snap, linktype = struct.unpack_from("<IHHiIII", raw)
    assert (magic, vmaj, vmin, linktype) == (0xA1B2C3D4, 2, 4, 256)
    off = 24
    sec, usec, incl, orig = struct.unpack_from("<IIII", raw, off)
    assert (sec, usec, incl) == (1_700_000_000, 500_000, 10 + len(adv.raw))
    ch, sig, _, _, aa, flags = struct.unpack_from("<BbbBIH", raw, off + 16)
    assert (ch, sig, aa) == (39, -70, ADV_ACCESS_ADDRESS)
    assert flags & 0x0C00 == 0x0C00 and (flags >> 7) & 7 == 0 and flags >> 14 == 1
    assert raw[off + 26:off + 26 + incl - 10] == adv.raw
    off += 16 + incl
    sec, usec, incl, _ = struct.unpack_from("<IIII", raw, off)
    assert (sec, usec) == (1_700_000_001, 250_000)
    *_, flags = struct.unpack_from("<BbbBIH", raw, off + 16)
    assert (flags >> 7) & 7 == 2   # data, central -> peripheral


# -- app capture -------------------------------------------------------------------
class FakeSession(QObject):
    operation = Signal(str, int, bytes, bool)
    value_updated = Signal(int, bytes, str)
    connection_changed = Signal(bool)
    log = Signal(str, str)

    address, name, mtu = PERIPH, "Thingy", 247

    def characteristic(self, handle):
        return SimpleNamespace(uuid="00002a37-0000-1000-8000-00805f9b34fb") if handle == 0x0F else None


def test_app_capture_records_session_traffic(qapp):
    store = PacketStore()
    cap = AppCapture(store)
    s = FakeSession()
    cap.attach_session(s)
    cap.attach_session(s)          # idempotent
    s.connection_changed.emit(True)
    s.operation.emit("write", 0x10, b"\x01\x02", False)
    s.value_updated.emit(0x10, b"\x01\x02", "write")     # duplicate of the operation: ignored
    s.value_updated.emit(0x0F, b"\x06\x48", "notify")
    s.log.emit("error", "Write failed: boom")
    s.log.emit("info", "not a packet")
    p = store.packets
    assert [x.info.split(",")[0] for x in p] == ["Connected", "Write Command", "Notification",
                                                 "Write failed: boom"]
    assert all(x.iface == "app" for x in p)
    assert (p[1].source, p[1].destination, p[1].raw) == ("BlueBench", PERIPH, b"\x01\x02")
    assert "Heart Rate Measurement" in p[2].info and p[2].source == PERIPH
    assert p[3].error
    cap.enabled = False
    s.operation.emit("read", 0x0F, b"\x00", True)
    assert len(store.packets) == 4


def test_store_trims_and_numbers(qapp):
    store = PacketStore(max_packets=100)
    resets = []
    store.reset.connect(lambda: resets.append(1))
    for _ in range(105):
        store.add(LLDecoder().decode(adv_ind(PERIPH, b""), meta(), 0))
    assert len(store.packets) <= 100 and resets
    assert store.packets[-1].number == 105


# -- air capture over a fake serial port ---------------------------------------------
class FakeSerial:
    """Answers PING only at ``answer_baud``; ``inject`` queues sniffer frames.
    ``no_cts``: writes time out, like a port whose other end never raises CTS."""

    def __init__(self, port, baud, answer_baud=1_000_000, no_cts=False):
        self.port, self.baud, self.answer, self.no_cts = port, baud, answer_baud, no_cts
        self.rx = deque()
        self.lock = threading.Lock()
        self.commands: list[ns.SnifferFrame] = []
        self._dec = ns.SlipDecoder()
        self.closed = False

    def reset_input_buffer(self):
        pass

    @property
    def in_waiting(self):
        return len(self.rx)

    def write(self, data):
        if self.no_cts:
            raise TimeoutError("Write timeout")      # what pyserial's write_timeout raises
        for raw in self._dec.feed(data):
            f = ns.parse_frame(raw)
            self.commands.append(f)
            if f.id == ns.PING_REQ and self.baud == self.answer:
                self.inject(bytes([6, 0, 1, 0, 0, ns.PING_RESP]))   # v1 header, empty payload
        return len(data)

    def inject(self, frame: bytes):
        with self.lock:
            self.rx.extend(ns.slip_encode(frame))

    def read(self, n):
        with self.lock:
            out = bytes(self.rx.popleft() for _ in range(min(n, len(self.rx))))
        if not out:
            time.sleep(0.005)
        return out

    def close(self):
        self.closed = True


def wait_for(cond, air, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        air.drain()
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_air_capture_probes_baud_scans_and_follows(qapp):
    ports: list[FakeSerial] = []

    def factory(port, baud):
        ports.append(FakeSerial(port, baud, answer_baud=460_800))
        return ports[-1]

    store = PacketStore()
    air = ns.AirCapture(store, serial_factory=factory)
    states = []
    air.state_changed.connect(states.append)
    air.start("COM9")
    assert wait_for(lambda: air.state == "scanning", air)
    assert [p.baud for p in ports] == [1_000_000, 460_800] and ports[0].closed
    ser = ports[-1]
    assert [c.id for c in ser.commands if c.id != ns.PING_REQ][:3] == \
        [ns.REQ_VERSION, ns.REQ_SCAN_CONT, ns.SET_TEMPORARY_KEY]

    ser.inject(sniffer_frame(adv_ind(PERIPH, b"\x07\x09Thingy"), ts=10))
    ser.inject(sniffer_frame(adv_ind(PERIPH, b""), ts=1_000_010))
    assert wait_for(lambda: len(store.packets) == 2, air)
    first, second = store.packets
    assert first.iface == "air" and first.info == 'ADV_IND "Thingy"'
    assert second.time - first.time == pytest.approx(1.0)
    assert air.advertisers[PERIPH].name == "Thingy" and air.advertisers[PERIPH].random

    air.follow(PERIPH)
    assert air.state == "following"
    follow = [c for c in ser.commands if c.id == ns.REQ_FOLLOW][-1]
    assert follow.payload == bytes.fromhex("C4123456789A") + b"\x01\x00"

    air.stop()
    assert air.state == "idle" and ser.closed
    assert ser.commands[-1].id == ns.GO_IDLE
    assert states[0] == "opening" and states[-1] == "idle"


def test_air_capture_reports_missing_sniffer(qapp):
    store = PacketStore()
    air = ns.AirCapture(store, serial_factory=lambda port, baud: FakeSerial(port, baud, answer_baud=0))
    msgs = []
    air.message.connect(lambda level, text: msgs.append((level, text)))
    air.start("COM3")
    assert wait_for(lambda: air.state == "idle", air)
    assert any(level == "error" and "No nRF Sniffer answered on COM3" in t for level, t in msgs)


def test_air_capture_reports_open_failure(qapp):
    def factory(port, baud):
        raise ns.SnifferError(f"Cannot open {port}: access denied")
    air = ns.AirCapture(PacketStore(), serial_factory=factory)
    msgs = []
    air.message.connect(lambda level, text: msgs.append(text))
    air.start("COM4")
    assert wait_for(lambda: air.state == "idle", air)
    assert "Cannot open COM4: access denied" in msgs


def test_list_ports_tells_the_dongle_bootloader_from_a_sniffer(monkeypatch):
    from serial.tools import list_ports as lp

    def port(dev, desc, vid, pid):
        return SimpleNamespace(device=dev, description=desc, vid=vid, pid=pid)
    monkeypatch.setattr(lp, "comports", lambda: [
        port("COM1", "Communications Port", None, None),
        port("COM3", "nRF52 SDFU USB (COM3)", 0x1915, 0x521F),     # Dongle, DFU bootloader
        port("COM9", "nRF Sniffer for Bluetooth LE", 0x1915, 0x522A)])
    ports = ns.list_ports()
    assert [(p.device, p.likely, p.bootloader) for p in ports] == [
        ("COM9", True, False), ("COM1", False, False), ("COM3", False, True)]
    assert "flash the nRF Sniffer firmware" in ports[2].label and "★" not in ports[2].label


def test_find_sniffer_pings_the_likely_ports():
    opened: list[FakeSerial] = []

    def factory(port, baud):
        if port == "COM6":
            raise ns.SnifferError("Cannot open COM6: access denied")
        opened.append(FakeSerial(port, baud, no_cts=port == "COM4"))
        return opened[-1]
    ports = [ns.PortInfo("COM1", "Serial", False), ns.PortInfo("COM6", "busy", True),
             ns.PortInfo("COM4", "USB Serial Device", True),
             ns.PortInfo("COM5", "USB Serial Device", True)]
    assert ns.find_sniffer(ports, factory) == "COM5"
    assert {s.port for s in opened} == {"COM4", "COM5"}          # COM1 is never touched
    assert all(s.closed for s in opened)
    assert ns.find_sniffer(ports[:3], factory) is None
