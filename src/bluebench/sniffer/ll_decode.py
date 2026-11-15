"""Decode BLE link-layer packets (access address + PDU + CRC) into CapturedPackets.

Covers what is useful when debugging a peripheral:
  advertising PDUs (with AD structures), CONNECT_IND, LL control PDUs,
  L2CAP with reassembly, ATT (with handle -> UUID learning from discovery),
  SMP and LE signaling.
Encrypted payloads are shown as such; decryption is not attempted.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

from ..ble import uuids
from ..ble.adv_parser import AdvField, parse_advertisement
from .model import AirMeta, CapturedPacket, Field

ADV_ACCESS_ADDRESS = 0x8E89BED6

ADV_TYPES = {
    0: "ADV_IND", 1: "ADV_DIRECT_IND", 2: "ADV_NONCONN_IND", 3: "SCAN_REQ", 4: "SCAN_RSP",
    5: "CONNECT_IND", 6: "ADV_SCAN_IND", 7: "ADV_EXT_IND",
}
LL_CONTROL = {
    0x00: "LL_CONNECTION_UPDATE_IND", 0x01: "LL_CHANNEL_MAP_IND", 0x02: "LL_TERMINATE_IND",
    0x03: "LL_ENC_REQ", 0x04: "LL_ENC_RSP", 0x05: "LL_START_ENC_REQ", 0x06: "LL_START_ENC_RSP",
    0x07: "LL_UNKNOWN_RSP", 0x08: "LL_FEATURE_REQ", 0x09: "LL_FEATURE_RSP",
    0x0A: "LL_PAUSE_ENC_REQ", 0x0B: "LL_PAUSE_ENC_RSP", 0x0C: "LL_VERSION_IND",
    0x0D: "LL_REJECT_IND", 0x0E: "LL_PERIPHERAL_FEATURE_REQ", 0x0F: "LL_CONNECTION_PARAM_REQ",
    0x10: "LL_CONNECTION_PARAM_RSP", 0x11: "LL_REJECT_EXT_IND", 0x12: "LL_PING_REQ",
    0x13: "LL_PING_RSP", 0x14: "LL_LENGTH_REQ", 0x15: "LL_LENGTH_RSP", 0x16: "LL_PHY_REQ",
    0x17: "LL_PHY_RSP", 0x18: "LL_PHY_UPDATE_IND", 0x19: "LL_MIN_USED_CHANNELS_IND",
}
HCI_ERRORS = {
    0x05: "Authentication Failure", 0x06: "PIN or Key Missing", 0x08: "Connection Timeout",
    0x13: "Remote User Terminated Connection", 0x14: "Remote Device Terminated (Low Resources)",
    0x15: "Remote Device Terminated (Power Off)", 0x16: "Connection Terminated By Local Host",
    0x1A: "Unsupported Remote Feature", 0x22: "LL Response Timeout", 0x28: "Instant Passed",
    0x3B: "Unacceptable Connection Parameters", 0x3D: "Connection Terminated (MIC Failure)",
    0x3E: "Connection Failed to be Established",
}
ATT_OPS = {
    0x01: "Error Response", 0x02: "Exchange MTU Request", 0x03: "Exchange MTU Response",
    0x04: "Find Information Request", 0x05: "Find Information Response",
    0x06: "Find By Type Value Request", 0x07: "Find By Type Value Response",
    0x08: "Read By Type Request", 0x09: "Read By Type Response", 0x0A: "Read Request",
    0x0B: "Read Response", 0x0C: "Read Blob Request", 0x0D: "Read Blob Response",
    0x0E: "Read Multiple Request", 0x0F: "Read Multiple Response",
    0x10: "Read By Group Type Request", 0x11: "Read By Group Type Response",
    0x12: "Write Request", 0x13: "Write Response", 0x16: "Prepare Write Request",
    0x17: "Prepare Write Response", 0x18: "Execute Write Request", 0x19: "Execute Write Response",
    0x1B: "Handle Value Notification", 0x1D: "Handle Value Indication",
    0x1E: "Handle Value Confirmation", 0x20: "Read Multiple Variable Request",
    0x21: "Read Multiple Variable Response", 0x23: "Multiple Handle Value Notification",
    0x52: "Write Command", 0xD2: "Signed Write Command",
}
ATT_ERRORS = {
    0x01: "Invalid Handle", 0x02: "Read Not Permitted", 0x03: "Write Not Permitted",
    0x04: "Invalid PDU", 0x05: "Insufficient Authentication", 0x06: "Request Not Supported",
    0x07: "Invalid Offset", 0x08: "Insufficient Authorization", 0x09: "Prepare Queue Full",
    0x0A: "Attribute Not Found", 0x0B: "Attribute Not Long",
    0x0C: "Encryption Key Size Too Short", 0x0D: "Invalid Attribute Value Length",
    0x0E: "Unlikely Error", 0x0F: "Insufficient Encryption", 0x10: "Unsupported Group Type",
    0x11: "Insufficient Resources", 0x12: "Database Out Of Sync", 0x13: "Value Not Allowed",
}
SMP_OPS = {
    0x01: "Pairing Request", 0x02: "Pairing Response", 0x03: "Pairing Confirm",
    0x04: "Pairing Random", 0x05: "Pairing Failed", 0x06: "Encryption Information",
    0x07: "Central Identification", 0x08: "Identity Information",
    0x09: "Identity Address Information", 0x0A: "Signing Information",
    0x0B: "Security Request", 0x0C: "Pairing Public Key", 0x0D: "Pairing DHKey Check",
    0x0E: "Pairing Keypress Notification",
}
SMP_IO = {0: "DisplayOnly", 1: "DisplayYesNo", 2: "KeyboardOnly", 3: "NoInputNoOutput",
          4: "KeyboardDisplay"}
SMP_FAIL = {
    0x01: "Passkey Entry Failed", 0x02: "OOB Not Available", 0x03: "Authentication Requirements",
    0x04: "Confirm Value Failed", 0x05: "Pairing Not Supported", 0x06: "Encryption Key Size",
    0x07: "Command Not Supported", 0x08: "Unspecified Reason", 0x09: "Repeated Attempts",
    0x0A: "Invalid Parameters", 0x0B: "DHKey Check Failed", 0x0C: "Numeric Comparison Failed",
}
SIGNALING = {
    0x01: "Command Reject", 0x06: "Disconnection Request", 0x07: "Disconnection Response",
    0x12: "Connection Parameter Update Request", 0x13: "Connection Parameter Update Response",
    0x14: "LE Credit Based Connection Request", 0x15: "LE Credit Based Connection Response",
    0x16: "Flow Control Credit",
}
AD_FLAGS = ["LE Limited Discoverable", "LE General Discoverable", "BR/EDR Not Supported",
            "Simultaneous LE and BR/EDR (Controller)", "Simultaneous LE and BR/EDR (Host)"]
GATT_PRIMARY, GATT_SECONDARY, GATT_CHAR = "2800", "2801", "2803"
GATT_DECLARATIONS = {"2800": "Primary Service", "2801": "Secondary Service", "2802": "Include",
                     "2803": "Characteristic"}


def addr(b: bytes) -> str:
    """On-air addresses are little-endian; display them MSB first."""
    return ":".join(f"{x:02X}" for x in reversed(b))


def hexs(b: bytes) -> str:
    return b.hex(" ").upper() if b else "(empty)"


def uuid_from(b: bytes) -> str:
    if len(b) == 2:
        return uuids.normalize(f"{int.from_bytes(b, 'little'):04x}")
    if len(b) == 16:
        return uuids.normalize(b[::-1].hex())
    return b.hex()


def uuid_label(u: str, kind: str = "characteristics") -> str:
    short = uuids.short_form(u)
    name = uuids.name_for(u, kind) or uuids.name_for(u, "services") \
        or uuids.name_for(u, "descriptors") or GATT_DECLARATIONS.get(short)
    return f"{name} (0x{short})" if name and len(short) <= 8 else (f"{name} ({short})" if name else short)


@dataclass
class Connection:
    access_address: int
    central: str = "Central"
    peripheral: str = "Peripheral"
    handles: dict[int, str] = field(default_factory=dict)     # handle -> UUID
    l2cap: dict[bool, bytearray] = field(default_factory=dict)  # per direction
    l2cap_expected: dict[bool, int] = field(default_factory=dict)
    encrypted: bool = False
    read_by_type: dict[bool, str] = field(default_factory=dict)  # last requested type per direction


class LLDecoder:
    """Stateful: remembers connections (from CONNECT_IND) and learned handles."""

    def __init__(self) -> None:
        self.connections: dict[int, Connection] = {}
        self.names: dict[str, str] = {}   # advertiser address -> local name

    # -- entry point ------------------------------------------------------
    def decode(self, ll: bytes, meta: AirMeta, time: float) -> CapturedPacket:
        pkt = CapturedPacket("air", time, "?", "?", "LE LL", "", raw=bytes(ll), air=meta)
        frame = Field("Frame", f"{len(ll)} bytes on channel {meta.channel}")
        frame.add("RSSI", f"{meta.rssi} dBm")
        frame.add("PHY", {0: "LE 1M", 1: "LE 2M", 2: "LE Coded"}.get(meta.phy, str(meta.phy)))
        frame.add("CRC", "OK" if meta.crc_ok else "BAD")
        frame.add("Sniffer timestamp", f"{meta.timestamp_us} µs")
        pkt.tree.append(frame)
        if len(ll) < 4 + 2 + 3:
            pkt.info = "Truncated packet"
            pkt.error = True
            return pkt
        aa = struct.unpack_from("<I", ll, 0)[0]
        pkt.tree.append(Field("Access Address", f"0x{aa:08X}", span=(0, 4)))
        try:
            if aa == ADV_ACCESS_ADDRESS:
                self._advertising(pkt, ll)
            else:
                self._data(pkt, ll, aa, meta)
        except (IndexError, struct.error, ValueError) as exc:
            pkt.info += f" [malformed: {exc}]"
            pkt.error = True
        pkt.tree.append(Field("CRC", ll[-3:].hex().upper(), span=(len(ll) - 3, len(ll))))
        if not meta.crc_ok:
            pkt.error = True
            pkt.info += " [CRC error]"
        return pkt

    # -- advertising channel --------------------------------------------------
    def _advertising(self, pkt: CapturedPacket, ll: bytes) -> None:
        h0, length = ll[4], ll[5]
        ptype, txadd, rxadd = h0 & 0x0F, bool(h0 & 0x40), bool(h0 & 0x80)
        name = ADV_TYPES.get(ptype, f"Unknown 0x{ptype:X}")
        payload = ll[6:6 + length]
        base = 6
        hdr = Field("Advertising PDU header", name, span=(4, 6))
        hdr.add("PDU type", f"{name} ({ptype})", span=(4, 5))
        hdr.add("TxAdd", "random" if txadd else "public", span=(4, 5))
        hdr.add("RxAdd", "random" if rxadd else "public", span=(4, 5))
        hdr.add("Length", str(length), span=(5, 6))
        pkt.tree.append(hdr)
        pkt.info = name
        body = Field(name, span=(base, base + len(payload)))
        pkt.tree.append(body)
        a = addr(payload[0:6])
        if ptype in (0, 2, 6, 4):   # AdvA + data
            body.add("AdvA", a, span=(base, base + 6))
            pkt.source, pkt.destination = a, "Broadcast"
            self._ad_structures(body, payload[6:], base + 6, pkt)
        elif ptype == 1:
            target = addr(payload[6:12])
            body.add("AdvA", a, span=(base, base + 6))
            body.add("TargetA", target, span=(base + 6, base + 12))
            pkt.source, pkt.destination = a, target
        elif ptype == 3:
            adv_a = addr(payload[6:12])
            body.add("ScanA", a, span=(base, base + 6))
            body.add("AdvA", adv_a, span=(base + 6, base + 12))
            pkt.source, pkt.destination = a, adv_a
        elif ptype == 5:
            self._connect_ind(pkt, body, payload, base)
        elif ptype == 7:
            self._ext_adv(pkt, body, payload, base)

    def _ad_structures(self, parent: Field, data: bytes, base: int, pkt: CapturedPacket) -> None:
        i, local_name, mfr, svc_data, svc_uuids, tx = 0, None, {}, {}, [], None
        while i < len(data):
            ln = data[i]
            if ln == 0:
                break
            t, val = data[i + 1], data[i + 2:i + 1 + ln]
            span = (base + i, base + i + 1 + ln)
            if t == 0x01 and val:
                flags = [n for b, n in enumerate(AD_FLAGS) if val[0] & (1 << b)]
                parent.add("Flags", ", ".join(flags) or "none", span=span)
            elif t in (0x08, 0x09):
                local_name = val.decode("utf-8", "replace")
            elif t in (0x02, 0x03):
                svc_uuids += [uuid_from(val[k:k + 2]) for k in range(0, len(val) - 1, 2)]
            elif t in (0x06, 0x07):
                svc_uuids += [uuid_from(val[k:k + 16]) for k in range(0, len(val) - 15, 16)]
            elif t == 0x0A and val:
                tx = struct.unpack("b", val[:1])[0]
            elif t == 0x16 and len(val) >= 2:
                svc_data[uuid_from(val[:2])] = bytes(val[2:])
            elif t == 0x21 and len(val) >= 16:
                svc_data[uuid_from(val[:16])] = bytes(val[16:])
            elif t == 0xFF and len(val) >= 2:
                mfr[int.from_bytes(val[:2], "little")] = bytes(val[2:])
            else:
                parent.add(f"AD type 0x{t:02X}", hexs(val), span=span)
            i += 1 + ln
        parsed = parse_advertisement(local_name, mfr, svc_data, svc_uuids, tx)
        for f in parsed.fields:
            parent.children.append(_from_adv_field(f))
        if local_name:
            pkt.info += f' "{local_name}"'
            self.names[pkt.source] = local_name
        if parsed.beacon:
            pkt.info += f" [{parsed.beacon}]"

    def _connect_ind(self, pkt: CapturedPacket, body: Field, p: bytes, base: int) -> None:
        init_a, adv_a = addr(p[0:6]), addr(p[6:12])
        aa, = struct.unpack_from("<I", p, 12)
        win_size = p[19]
        win_off, interval, latency, timeout = struct.unpack_from("<HHHH", p, 20)
        chm = int.from_bytes(p[28:33], "little")
        hop, sca = p[33] & 0x1F, p[33] >> 5
        body.add("InitA", init_a, span=(base, base + 6))
        body.add("AdvA", adv_a, span=(base + 6, base + 12))
        ll = body.add("LLData", span=(base + 12, base + 34))
        ll.add("Access Address", f"0x{aa:08X}", span=(base + 12, base + 16))
        ll.add("CRC Init", p[16:19][::-1].hex().upper(), span=(base + 16, base + 19))
        ll.add("Window Size", f"{win_size * 1.25:g} ms", span=(base + 19, base + 20))
        ll.add("Window Offset", f"{win_off * 1.25:g} ms", span=(base + 20, base + 22))
        ll.add("Interval", f"{interval * 1.25:g} ms", span=(base + 22, base + 24))
        ll.add("Peripheral Latency", str(latency), span=(base + 24, base + 26))
        ll.add("Supervision Timeout", f"{timeout * 10} ms", span=(base + 26, base + 28))
        ll.add("Channel Map", f"{bin(chm).count('1')} channels used", span=(base + 28, base + 33))
        ll.add("Hop", str(hop), span=(base + 33, base + 34))
        ll.add("SCA", str(sca), span=(base + 33, base + 34))
        self.connections[aa] = Connection(aa, init_a, adv_a)
        pkt.source, pkt.destination = init_a, adv_a
        pkt.info = f"CONNECT_IND, interval {interval * 1.25:g} ms, AA 0x{aa:08X}"

    def _ext_adv(self, pkt: CapturedPacket, body: Field, p: bytes, base: int) -> None:
        hlen, mode = p[0] & 0x3F, p[0] >> 6
        body.add("AdvMode", {0: "non-connectable, non-scannable", 1: "connectable",
                             2: "scannable"}.get(mode, str(mode)), span=(base, base + 1))
        if hlen:
            flags = p[1]
            i = 2
            if flags & 0x01:
                a = addr(p[i:i + 6])
                body.add("AdvA", a, span=(base + i, base + i + 6))
                pkt.source, pkt.destination = a, "Broadcast"
                i += 6
            if flags & 0x02:
                body.add("TargetA", addr(p[i:i + 6]), span=(base + i, base + i + 6))
                i += 6
            if flags & 0x04:
                i += 1  # CTEInfo
            if flags & 0x08:
                did_sid, = struct.unpack_from("<H", p, i)
                body.add("ADI", f"SID {did_sid >> 12}, DID 0x{did_sid & 0xFFF:03X}", span=(base + i, base + i + 2))
                i += 2
            if flags & 0x10:
                chan = p[i] & 0x3F
                body.add("AuxPtr", f"channel {chan}", span=(base + i, base + i + 3))
                pkt.info += f" → AUX on ch {chan}"
        if pkt.source == "?":
            pkt.source, pkt.destination = "(anonymous)", "Broadcast"

    # -- data channel -----------------------------------------------------------
    def _data(self, pkt: CapturedPacket, ll: bytes, aa: int, meta: AirMeta) -> None:
        conn = self.connections.setdefault(aa, Connection(aa))
        c2p = meta.central_to_peripheral
        pkt.source, pkt.destination = (conn.central, conn.peripheral) if c2p else \
            (conn.peripheral, conn.central)
        h0, length = ll[4], ll[5]
        llid, nesn, sn, md, cp = h0 & 0x03, (h0 >> 2) & 1, (h0 >> 3) & 1, (h0 >> 4) & 1, (h0 >> 5) & 1
        hdr = Field("Data PDU header", span=(4, 6))
        hdr.add("LLID", {1: "Continuation / empty", 2: "L2CAP start", 3: "LL control"}.get(llid, str(llid)),
                span=(4, 5))
        hdr.add("NESN / SN / MD", f"{nesn} / {sn} / {md}", span=(4, 5))
        hdr.add("Length", str(length), span=(5, 6))
        hdr.add("Direction", "Central → Peripheral" if c2p else "Peripheral → Central")
        pkt.tree.append(hdr)
        start = 6 + (1 if cp else 0)
        payload = ll[start:start + length]
        if meta.encrypted and length:
            if not meta.mic_ok:
                # No key (or wrong key): the payload is ciphertext.
                pkt.info = f"Encrypted packet ({length} bytes)"
                pkt.tree.append(Field("Encrypted payload", f"{length} bytes",
                                      span=(start, start + length)))
                return
            # The sniffer decrypted it (e.g. legacy Just Works); last 4 bytes are the MIC.
            hdr.add("Decrypted", "yes (MIC OK)")
            payload = payload[:-4]
            pkt.tree.append(Field("MIC", ll[start + length - 4:start + length].hex().upper(),
                                  span=(start + length - 4, start + length)))
            length -= 4
        if length == 0:
            pkt.empty = True
            pkt.info = "Empty PDU"
            return
        if llid == 3:
            self._ll_control(pkt, payload, start, conn)
        elif llid == 2:
            self._l2cap_start(pkt, payload, start, conn, c2p)
        elif llid == 1:
            self._l2cap_cont(pkt, payload, start, conn, c2p)

    def _ll_control(self, pkt: CapturedPacket, p: bytes, base: int, conn: Connection) -> None:
        op = p[0]
        name = LL_CONTROL.get(op, f"LL control 0x{op:02X}")
        pkt.protocol, pkt.info = "LE LL", name
        f = Field("LL Control", name, span=(base, base + len(p)))
        pkt.tree.append(f)
        a = p[1:]
        if op == 0x02 and a:
            reason = HCI_ERRORS.get(a[0], f"0x{a[0]:02X}")
            f.add("Error code", reason, span=(base + 1, base + 2))
            pkt.info += f" ({reason})"
        elif op == 0x0C and len(a) >= 5:
            vers, comp, sub = a[0], int.from_bytes(a[1:3], "little"), int.from_bytes(a[3:5], "little")
            company = uuids.company_name(comp) or f"0x{comp:04X}"
            ver = {9: "5.0", 10: "5.1", 11: "5.2", 12: "5.3", 13: "5.4", 14: "6.0", 8: "4.2",
                   7: "4.1", 6: "4.0"}.get(vers, str(vers))
            f.add("Bluetooth version", ver, span=(base + 1, base + 2))
            f.add("Company", company, span=(base + 2, base + 4))
            f.add("Subversion", f"0x{sub:04X}", span=(base + 4, base + 6))
            pkt.info += f" (Bluetooth {ver}, {company})"
        elif op in (0x14, 0x15) and len(a) >= 8:
            rx_oct, rx_t, tx_oct, tx_t = struct.unpack_from("<HHHH", a)
            f.add("Max RX octets / time", f"{rx_oct} / {rx_t} µs")
            f.add("Max TX octets / time", f"{tx_oct} / {tx_t} µs")
            pkt.info += f" (TX {tx_oct} B, RX {rx_oct} B)"
        elif op in (0x16, 0x17) and len(a) >= 2:
            f.add("TX PHYs", _phys(a[0]))
            f.add("RX PHYs", _phys(a[1]))
            pkt.info += f" (TX {_phys(a[0])}, RX {_phys(a[1])})"
        elif op == 0x18 and len(a) >= 4:
            pkt.info += f" (C→P {_phys(a[0])}, P→C {_phys(a[1])})"
        elif op == 0x00 and len(a) >= 11:
            ws, wo, iv, lat, to, inst = struct.unpack_from("<BHHHHH", a)
            f.add("Interval", f"{iv * 1.25:g} ms")
            f.add("Latency", str(lat))
            f.add("Timeout", f"{to * 10} ms")
            f.add("Instant", str(inst))
            pkt.info += f" (interval {iv * 1.25:g} ms at instant {inst})"
        elif op in (0x05, 0x06):
            conn.encrypted = True   # everything after START_ENC is encrypted
        elif op in (0x07, 0x0D) and a:
            f.add("Detail", f"0x{a[0]:02X}")

    def _l2cap_start(self, pkt: CapturedPacket, p: bytes, base: int, conn: Connection,
                     c2p: bool) -> None:
        if len(p) < 4:
            pkt.protocol, pkt.info = "L2CAP", "Truncated L2CAP header"
            return
        total = struct.unpack_from("<H", p)[0] + 4
        if len(p) < total:
            conn.l2cap[c2p] = bytearray(p)
            conn.l2cap_expected[c2p] = total
            pkt.protocol, pkt.info = "L2CAP", f"L2CAP fragment (start, {len(p)}/{total} bytes)"
            return
        self._l2cap(pkt, bytes(p[:total]), base)

    def _l2cap_cont(self, pkt: CapturedPacket, p: bytes, base: int, conn: Connection,
                    c2p: bool) -> None:
        buf = conn.l2cap.get(c2p)
        if buf is None:
            pkt.protocol, pkt.info = "L2CAP", "L2CAP continuation (start not captured)"
            return
        buf += p
        total = conn.l2cap_expected[c2p]
        if len(buf) < total:
            pkt.protocol, pkt.info = "L2CAP", f"L2CAP fragment ({len(buf)}/{total} bytes)"
            return
        del conn.l2cap[c2p]
        pkt.tree.append(Field("Reassembled L2CAP", f"{total} bytes from several packets"))
        self._l2cap(pkt, bytes(buf[:total]), None)

    def _l2cap(self, pkt: CapturedPacket, p: bytes, base: int | None) -> None:
        length, cid = struct.unpack_from("<HH", p)
        sp = (lambda a, b: (base + a, base + b)) if base is not None else (lambda a, b: None)
        f = Field("L2CAP", f"CID 0x{cid:04X}, {length} bytes", span=sp(0, 4))
        pkt.tree.append(f)
        data = p[4:]
        conn = self.connections.get(struct.unpack_from("<I", pkt.raw)[0])
        if cid == 0x0004:
            self._att(pkt, data, sp, conn)
        elif cid == 0x0006:
            self._smp(pkt, data, sp)
        elif cid == 0x0005 and data:
            pkt.protocol = "L2CAP"
            pkt.info = SIGNALING.get(data[0], f"Signaling 0x{data[0]:02X}")
            sig = Field("LE Signaling", pkt.info, span=sp(4, 4 + len(data)))
            if data[0] == 0x12 and len(data) >= 12:
                imin, imax, lat, to = struct.unpack_from("<HHHH", data, 4)
                sig.add("Interval", f"{imin * 1.25:g}–{imax * 1.25:g} ms")
                sig.add("Latency", str(lat))
                sig.add("Timeout", f"{to * 10} ms")
                pkt.info += f" ({imin * 1.25:g}–{imax * 1.25:g} ms)"
            pkt.tree.append(sig)
        else:
            pkt.protocol, pkt.info = "L2CAP", f"L2CAP CID 0x{cid:04X}"

    # -- ATT -----------------------------------------------------------------
    def _att(self, pkt: CapturedPacket, d: bytes, sp, conn: Connection | None) -> None:
        pkt.protocol = "ATT"
        op = d[0]
        name = ATT_OPS.get(op, f"ATT 0x{op:02X}")
        f = Field("Attribute Protocol", name, span=sp(4, 4 + len(d)))
        pkt.tree.append(f)
        f.add("Opcode", f"{name} (0x{op:02X})", span=sp(4, 5))
        a = d[1:]
        o = 5  # offset of `a` within the L2CAP frame
        handles = conn.handles if conn else {}

        def handle_text(h: int) -> str:
            u = handles.get(h)
            return f"0x{h:04X}" + (f" ({uuid_label(u)})" if u else "")

        info = name
        if op == 0x01 and len(a) >= 4:
            req, h, err = a[0], int.from_bytes(a[1:3], "little"), a[3]
            ename = ATT_ERRORS.get(err, f"0x{err:02X}")
            f.add("Request in error", ATT_OPS.get(req, f"0x{req:02X}"), span=sp(o, o + 1))
            f.add("Handle", handle_text(h), span=sp(o + 1, o + 3))
            f.add("Error", ename, span=sp(o + 3, o + 4))
            info = f"Error Response - {ename}, Handle: {handle_text(h)}"
            pkt.error = True
        elif op in (0x02, 0x03) and len(a) >= 2:
            mtu = int.from_bytes(a[:2], "little")
            f.add("MTU", str(mtu), span=sp(o, o + 2))
            info += f", MTU: {mtu}"
        elif op in (0x0A, 0x0C, 0x12, 0x52, 0x1B, 0x1D, 0x16, 0xD2) and len(a) >= 2:
            h = int.from_bytes(a[:2], "little")
            f.add("Handle", handle_text(h), span=sp(o, o + 2))
            info += f", Handle: {handle_text(h)}"
            vo = o + 2
            if op in (0x0C, 0x16):
                f.add("Offset", str(int.from_bytes(a[2:4], "little")), span=sp(o + 2, o + 4))
                vo += 2
            value = d[vo - 4:]
            if op not in (0x0A, 0x0C):
                f.add("Value", _value_text(value), span=sp(vo, 4 + len(d)))
                if value:
                    info += f", Value: {hexs(value[:12])}{'…' if len(value) > 12 else ''}"
        elif op in (0x0B, 0x0D) and True:
            f.add("Value", _value_text(a), span=sp(o, o + len(a)))
            info += f", Value: {hexs(a[:12])}{'…' if len(a) > 12 else ''}"
        elif op in (0x04, 0x08, 0x10) and len(a) >= 4:
            if op == 0x08 and conn and len(a) > 4:
                # The response comes the other way; remember what was asked for.
                conn.read_by_type[pkt.source != conn.central] = uuids.short_form(uuid_from(a[4:]))
            s, e = struct.unpack_from("<HH", a)
            f.add("Handle range", f"0x{s:04X}–0x{e:04X}", span=sp(o, o + 4))
            info += f", Handles: 0x{s:04X}..0x{e:04X}"
            if op != 0x04 and len(a) > 4:
                u = uuid_from(a[4:])
                f.add("Attribute type", uuid_label(u, "services"), span=sp(o + 4, o + len(a)))
                info += f", {uuid_label(u, 'services')}"
        elif op == 0x11 and a:
            each = a[0]
            for k in range(1, len(a) - each + 1, each) if each else ():
                s, e = struct.unpack_from("<HH", a, k)
                u = uuid_from(a[k + 4:k + each])
                f.add("Service", f"0x{s:04X}–0x{e:04X}: {uuid_label(u, 'services')}",
                      span=sp(o + k, o + k + each))
                handles[s] = u
            info += f" ({(len(a) - 1) // each if each else 0} services)"
        elif op == 0x09 and a:
            each = a[0]
            n_chars = 0
            for k in range(1, len(a) - each + 1, each) if each else ():
                h = int.from_bytes(a[k:k + 2], "little")
                val = a[k + 2:k + each]
                asked = conn.read_by_type.get(pkt.source == conn.central) if conn else None
                if each in (7, 21) and asked in (None, GATT_CHAR):  # characteristic declaration
                    props, vh = val[0], int.from_bytes(val[1:3], "little")
                    u = uuid_from(val[3:])
                    handles[vh] = u
                    n_chars += 1
                    f.add("Characteristic", f"0x{vh:04X}: {uuid_label(u)} ({_props(props)})",
                          span=sp(o + k, o + k + each))
                else:
                    f.add(f"Handle 0x{h:04X}", _value_text(val), span=sp(o + k, o + k + each))
            if n_chars:
                info += f" ({n_chars} characteristic{'s' if n_chars > 1 else ''})"
        elif op == 0x05 and a:
            each = 4 if a[0] == 1 else 18
            for k in range(1, len(a) - each + 1, each):
                h = int.from_bytes(a[k:k + 2], "little")
                u = uuid_from(a[k + 2:k + each])
                handles.setdefault(h, u)
                f.add(f"Handle 0x{h:04X}", uuid_label(u, "descriptors"), span=sp(o + k, o + k + each))
        if conn:
            arrow = "Sent" if pkt.source == conn.central else "Rcvd"
            info = f"{arrow} {info}"
        pkt.info = info

    # -- SMP ----------------------------------------------------------------
    def _smp(self, pkt: CapturedPacket, d: bytes, sp) -> None:
        pkt.protocol = "SMP"
        op = d[0]
        name = SMP_OPS.get(op, f"SMP 0x{op:02X}")
        f = Field("Security Manager Protocol", name, span=sp(4, 4 + len(d)))
        pkt.tree.append(f)
        info = name
        if op in (0x01, 0x02) and len(d) >= 7:
            io, oob, auth, keysize = d[1], d[2], d[3], d[4]
            f.add("IO Capability", SMP_IO.get(io, str(io)), span=sp(5, 6))
            f.add("OOB data", "present" if oob else "not present", span=sp(6, 7))
            bits = [n for b, n in ((0x03, "Bonding"), (0x04, "MITM"), (0x08, "Secure Connections"),
                                   (0x10, "Keypress")) if auth & b]
            f.add("AuthReq", ", ".join(bits) or "none", span=sp(7, 8))
            f.add("Max key size", str(keysize), span=sp(8, 9))
            info += f": {SMP_IO.get(io, io)}, {'SC' if auth & 0x08 else 'Legacy'}" \
                    f"{', MITM' if auth & 0x04 else ''}{', Bonding' if auth & 0x03 else ''}"
        elif op == 0x05 and len(d) >= 2:
            reason = SMP_FAIL.get(d[1], f"0x{d[1]:02X}")
            f.add("Reason", reason, span=sp(5, 6))
            info += f": {reason}"
            pkt.error = True
        elif op == 0x0B and len(d) >= 2:
            info += f" (AuthReq 0x{d[1]:02X})"
        pkt.info = info


def _phys(bits: int) -> str:
    names = [n for b, n in ((1, "1M"), (2, "2M"), (4, "Coded")) if bits & b]
    return "+".join(names) or "none"


def _props(p: int) -> str:
    names = [(0x02, "R"), (0x04, "WNR"), (0x08, "W"), (0x10, "N"), (0x20, "I")]
    return " ".join(n for b, n in names if p & b) or "-"


def _value_text(v: bytes) -> str:
    text = hexs(v)
    if v and all(32 <= c < 127 for c in v):
        text += f'  "{v.decode()}"'
    return text


def _from_adv_field(f: AdvField) -> Field:
    out = Field(f.label, f.value)
    out.children = [_from_adv_field(c) for c in f.children]
    return out
