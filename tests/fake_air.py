"""On-air BLE packet and nRF Sniffer frame builders for tests and guide screenshots."""
from __future__ import annotations

import struct

from bluebench.sniffer import nrf_sniffer as ns
from bluebench.sniffer.ll_decode import ADV_ACCESS_ADDRESS
from bluebench.sniffer.model import AirMeta

ADV_AA = struct.pack("<I", ADV_ACCESS_ADDRESS)
CONN_AA = 0x50654A1B
CRC = b"\x11\x22\x33"
PERIPH = "C4:12:34:56:78:9A"
CENTRAL = "00:1A:7D:DA:71:13"


def mac(a: str) -> bytes:
    return bytes.fromhex(a.replace(":", ""))[::-1]


def adv_ind(address: str, ad: bytes, ptype: int = 0, random: bool = True) -> bytes:
    payload = mac(address) + ad
    return ADV_AA + bytes([ptype | (0x40 if random else 0), len(payload)]) + payload + CRC


def connect_ind(init: str, adv: str, aa: int = CONN_AA, interval: int = 24) -> bytes:
    lldata = struct.pack("<I", aa) + b"\x55\x66\x77" + bytes([2]) + struct.pack(
        "<HHHH", 3, interval, 0, 400) + b"\xff\xff\xff\xff\x1f" + bytes([0x25])
    payload = mac(init) + mac(adv) + lldata
    return ADV_AA + bytes([0x05 | 0x40, len(payload)]) + payload + CRC


def data_pdu(llid: int, payload: bytes, aa: int = CONN_AA) -> bytes:
    return struct.pack("<I", aa) + bytes([llid, len(payload)]) + payload + CRC


def l2cap(cid: int, data: bytes) -> bytes:
    return struct.pack("<HH", len(data), cid) + data


C2P = 0x01 | 0x02     # CRC ok, central -> peripheral
P2C = 0x01


def meta(flags: int = P2C, channel: int = 37) -> AirMeta:
    return AirMeta(channel, -55, flags, 1000)


def sniffer_frame(ll: bytes, flags: int = 0x01, channel: int = 37, rssi: int = -60,
                  ts: int = 1000, protover: int = 3, coded_ci: int | None = None,
                  pid: int = ns.EVENT_PACKET_ADV_PDU) -> bytes:
    if coded_ci is None:
        ble = ll[:6] + b"\xEE" + ll[6:]                       # padding after the header
    else:
        ble = ll[:4] + bytes([coded_ci]) + ll[4:6] + b"\xEE" + ll[6:]
    payload = bytes([10, flags, channel, -rssi]) + struct.pack("<HI", 7, ts) + ble
    header = struct.pack("<H", len(payload)) + bytes([protover]) + struct.pack("<H", 1) + bytes([pid])
    return header + payload
