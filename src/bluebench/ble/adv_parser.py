"""Turn advertising data into labelled, human-readable fields.

Pure functions with no bleak or Qt imports, so they can be unit-tested
without hardware. ``parse_advertisement`` takes the same pieces bleak's
``AdvertisementData`` exposes.
"""
from __future__ import annotations

import struct
import uuid as _uuid
from dataclasses import dataclass, field

from . import registry, uuids

APPLE = 0x004C
EDDYSTONE_UUID = uuids.normalize("feaa")

_EDDYSTONE_URL_SCHEMES = ["http://www.", "https://www.", "http://", "https://"]
_EDDYSTONE_URL_EXPANSIONS = [
    ".com/", ".org/", ".edu/", ".net/", ".info/", ".biz/", ".gov/",
    ".com", ".org", ".edu", ".net", ".info", ".biz", ".gov",
]


@dataclass
class AdvField:
    label: str
    value: str
    children: list["AdvField"] = field(default_factory=list)


@dataclass
class ParsedAdvertisement:
    local_name: str | None
    tx_power: int | None
    service_uuids: list[str]
    fields: list[AdvField]
    beacon: str | None = None  # "iBeacon", "Eddystone-UID", ... when recognised
    # company ID -> payload, as bleak reports it (company ID stripped from the bytes)
    manufacturer_data: dict[int, bytes] = field(default_factory=dict)
    service_data: dict[str, bytes] = field(default_factory=dict)  # normalized UUID -> payload
    # From registered decoders (see registry.Decoded): stable across address changes.
    identity: tuple | None = None
    similarity: tuple | None = None


def hexstr(data: bytes) -> str:
    return "0x" + data.hex().upper() if data else "(empty)"


def manufacturer_raw(company_id: int, payload: bytes) -> bytes:
    """Manufacturer Specific Data as sent on air: company ID little-endian, then the payload."""
    return company_id.to_bytes(2, "little") + payload


def parse_advertisement(
    local_name: str | None,
    manufacturer_data: dict[int, bytes],
    service_data: dict[str, bytes],
    service_uuids: list[str],
    tx_power: int | None,
) -> ParsedAdvertisement:
    fields: list[AdvField] = []
    beacon: str | None = None

    if local_name:
        fields.append(AdvField("Complete Local Name", local_name))
    if tx_power is not None:
        fields.append(AdvField("TX Power Level", f"{tx_power} dBm"))

    norm_uuids = [uuids.normalize(u) for u in service_uuids]
    if norm_uuids:
        fields.append(AdvField(
            "Service UUIDs",
            f"{len(norm_uuids)}",
            [AdvField(uuids.short_form(u), uuids.display_name(u, "services")) for u in norm_uuids],
        ))

    for company_id, payload in manufacturer_data.items():
        f = AdvField(
            "Manufacturer Data",
            f"{uuids.company_name(company_id)} <0x{company_id:04X}>",
            [AdvField("Data", hexstr(payload)),
             AdvField("Raw (on air)", hexstr(manufacturer_raw(company_id, payload)))],
        )
        if company_id == APPLE:
            ib = decode_ibeacon(payload)
            if ib:
                beacon = "iBeacon"
                f.children.extend(ib)
        fields.append(f)

    for svc_uuid, payload in service_data.items():
        norm = uuids.normalize(svc_uuid)
        f = AdvField(
            "Service Data",
            f"{uuids.display_name(norm, 'services')} <{uuids.short_form(norm)}>",
            [AdvField("Data", hexstr(payload))],
        )
        if norm == EDDYSTONE_UUID:
            kind, children = decode_eddystone(payload)
            if kind:
                beacon = kind
                f.children.extend(children)
        fields.append(f)

    adv = ParsedAdvertisement(local_name, tx_power, norm_uuids, fields, beacon,
                              {int(k): bytes(v) for k, v in manufacturer_data.items()},
                              {uuids.normalize(k): bytes(v) for k, v in service_data.items()})
    for d in registry.decode(adv):
        adv.fields.extend(d.fields)
        adv.beacon = adv.beacon or d.label
        adv.identity = adv.identity or d.identity
        adv.similarity = adv.similarity or d.similarity
    return adv


def decode_ibeacon(payload: bytes) -> list[AdvField] | None:
    """Apple iBeacon: 02 15 <uuid16> <major u16be> <minor u16be> <tx i8>."""
    if len(payload) != 23 or payload[0] != 0x02 or payload[1] != 0x15:
        return None
    proximity = _uuid.UUID(bytes=payload[2:18])
    major, minor, tx = struct.unpack(">HHb", payload[18:23])
    return [
        AdvField("Beacon", "iBeacon"),
        AdvField("Proximity UUID", str(proximity).upper()),
        AdvField("Major", str(major)),
        AdvField("Minor", str(minor)),
        AdvField("Measured Power", f"{tx} dBm @ 1 m"),
    ]


def decode_eddystone(payload: bytes) -> tuple[str | None, list[AdvField]]:
    if not payload:
        return None, []
    frame = payload[0]
    if frame == 0x00 and len(payload) >= 18:
        tx = struct.unpack("b", payload[1:2])[0]
        return "Eddystone-UID", [
            AdvField("Beacon", "Eddystone-UID"),
            AdvField("TX Power @ 0 m", f"{tx} dBm"),
            AdvField("Namespace", hexstr(payload[2:12])),
            AdvField("Instance", hexstr(payload[12:18])),
        ]
    if frame == 0x10 and len(payload) >= 3:
        tx = struct.unpack("b", payload[1:2])[0]
        scheme = payload[2]
        if scheme >= len(_EDDYSTONE_URL_SCHEMES):
            return None, []
        url = _EDDYSTONE_URL_SCHEMES[scheme]
        for b in payload[3:]:
            url += _EDDYSTONE_URL_EXPANSIONS[b] if b < len(_EDDYSTONE_URL_EXPANSIONS) else chr(b)
        return "Eddystone-URL", [
            AdvField("Beacon", "Eddystone-URL"),
            AdvField("TX Power @ 0 m", f"{tx} dBm"),
            AdvField("URL", url),
        ]
    if frame == 0x20 and len(payload) >= 14 and payload[1] == 0x00:
        vbatt, temp_raw, adv_cnt, sec_cnt = struct.unpack(">HhII", payload[2:14])
        return "Eddystone-TLM", [
            AdvField("Beacon", "Eddystone-TLM"),
            AdvField("Battery", f"{vbatt} mV"),
            AdvField("Temperature", f"{temp_raw / 256:.2f} °C"),
            AdvField("Adv Count", str(adv_cnt)),
            AdvField("Uptime", f"{sec_cnt / 10:.1f} s"),
        ]
    return None, []
