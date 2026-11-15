"""Write sniffed packets as pcap that Wireshark opens directly.

LINKTYPE_BLUETOOTH_LE_LL_WITH_PHDR (256): a 10-byte pseudo-header
(channel, signal, noise, AA offenses, reference AA, flags) then the on-air
packet. See https://www.tcpdump.org/linktypes/LINKTYPE_BLUETOOTH_LE_LL_WITH_PHDR.html
"""
from __future__ import annotations

import struct
from collections.abc import Iterable
from pathlib import Path

from .ll_decode import ADV_ACCESS_ADDRESS
from .model import CapturedPacket

LINKTYPE_BLUETOOTH_LE_LL_WITH_PHDR = 256
PCAP_MAGIC = 0xA1B2C3D4

F_DEWHITENED = 0x0001
F_SIGNAL_VALID = 0x0002
F_DECRYPTED = 0x0008
F_REF_AA_VALID = 0x0010
F_CRC_CHECKED = 0x0400
F_CRC_VALID = 0x0800
F_MIC_CHECKED = 0x1000
F_MIC_VALID = 0x2000
PDU_ADV, PDU_DATA_C2P, PDU_DATA_P2C = 0, 2, 3


def global_header() -> bytes:
    return struct.pack("<IHHiIII", PCAP_MAGIC, 2, 4, 0, 0, 65535, LINKTYPE_BLUETOOTH_LE_LL_WITH_PHDR)


def record(pkt: CapturedPacket, epoch: float) -> bytes:
    meta, ll = pkt.air, pkt.raw
    assert meta is not None
    aa = struct.unpack_from("<I", ll)[0]
    flags = F_DEWHITENED | F_SIGNAL_VALID | F_REF_AA_VALID | F_CRC_CHECKED
    if meta.crc_ok:
        flags |= F_CRC_VALID
    if aa == ADV_ACCESS_ADDRESS:
        pdu = PDU_ADV
    else:
        pdu = PDU_DATA_C2P if meta.central_to_peripheral else PDU_DATA_P2C
        if meta.encrypted:
            flags |= F_MIC_CHECKED
            if meta.mic_ok:
                flags |= F_MIC_VALID | F_DECRYPTED
    flags |= pdu << 7
    flags |= (meta.phy & 0x03) << 14
    if meta.phy == 2 and meta.coding_indicator is not None:
        ll = ll[:4] + bytes([meta.coding_indicator]) + ll[4:]
    signal = max(-128, min(127, meta.rssi))
    phdr = struct.pack("<BbbBIH", meta.channel, signal, 0, 0, aa, flags)
    data = phdr + ll
    ts = epoch + pkt.time
    sec = int(ts)
    usec = int(round((ts - sec) * 1e6))
    if usec >= 1_000_000:
        sec, usec = sec + 1, usec - 1_000_000
    return struct.pack("<IIII", sec, usec, len(data), len(data)) + data


def write(path: str | Path, packets: Iterable[CapturedPacket], epoch: float) -> int:
    """Write the on-air packets among ``packets``; returns how many were written."""
    n = 0
    with open(path, "wb") as f:
        f.write(global_header())
        for p in packets:
            if p.air is not None and len(p.raw) >= 9:
                f.write(record(p, epoch))
                n += 1
    return n
