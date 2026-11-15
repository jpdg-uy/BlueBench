"""Advertisement filters shared by the scanner UI, the Python API and the CLI (no Qt)."""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import uuids
from .adv_parser import manufacturer_raw

_HEX_SEPARATORS = re.compile(r"[\s:\-]")


def hex_query(text: str) -> str | None:
    """The text as lowercase hex digits ("5900", "0x5900", "59 00"...), else None."""
    t = _HEX_SEPARATORS.sub("", text.strip().lower())
    if t.startswith("0x"):
        t = t[2:]
    if len(t) >= 2 and len(t) % 2 == 0 and all(c in "0123456789abcdef" for c in t):
        return t
    return None


def manufacturer_matches(manufacturer_data: dict[int, bytes], query: str) -> bool:
    """Whether some Manufacturer Specific Data starts with the hex bytes in ``query``.

    Matches the bytes as sent on air (company ID little-endian, e.g. 59 00 ...),
    and also the way the company ID is usually written (0x0059 → "0059...").
    """
    for company_id, payload in manufacturer_data.items():
        on_air = manufacturer_raw(company_id, payload).hex()
        written = f"{company_id:04x}{payload.hex()}"
        if on_air.startswith(query) or written.startswith(query):
            return True
    return False


@dataclass(frozen=True)
class DeviceFilter:
    """Criteria for picking devices out of a scan. Unset fields match anything.

    name     case-insensitive substring of the advertised name (whole name if
             ``exact_name``)
    address  exact address (case-insensitive)
    mfr      hex prefix of manufacturer data, see ``manufacturer_matches``
    service  advertised service UUID (short "180d" or full)
    min_rssi weakest signal accepted, in dBm
    """

    name: str | None = None
    address: str | None = None
    mfr: str | None = None
    service: str | None = None
    min_rssi: int | None = None
    exact_name: bool = False

    def __post_init__(self) -> None:
        if self.mfr is not None and hex_query(self.mfr) is None:
            raise ValueError(f"Manufacturer filter must be hex bytes, e.g. 5900 (got '{self.mfr}')")

    @property
    def is_empty(self) -> bool:
        return all(v is None for v in (self.name, self.address, self.mfr, self.service,
                                       self.min_rssi))

    def matches(self, *, address: str, name: str | None, rssi: int,
                manufacturer_data: dict[int, bytes], service_uuids: list[str]) -> bool:
        if self.address is not None and address.lower() != self.address.lower():
            return False
        if self.name is not None:
            want, have = self.name.lower(), (name or "").lower()
            if (want != have) if self.exact_name else (want not in have):
                return False
        if self.min_rssi is not None and rssi < self.min_rssi:
            return False
        if self.mfr is not None and not manufacturer_matches(manufacturer_data,
                                                              hex_query(self.mfr) or ""):
            return False
        if self.service is not None:
            want = uuids.normalize(self.service)
            if want not in (uuids.normalize(u) for u in service_uuids):
                return False
        return True
