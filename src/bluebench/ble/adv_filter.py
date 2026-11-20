"""Advertisement filters shared by the scanner UI, the Python API and the CLI (no Qt)."""
from __future__ import annotations

import functools
import re
from dataclasses import dataclass

from . import registry, uuids
from .adv_parser import ParsedAdvertisement, manufacturer_raw

_HEX_SEPARATORS = re.compile(r"[\s:\-]")
_PATTERN_CHARS = frozenset("0123456789abcdef?")


@functools.lru_cache(maxsize=64)
def byte_pattern(text: str) -> re.Pattern[str] | None:
    """The text as a pattern of hex bytes, else None.

    Plain bytes ("5900", "0x5900", "59 00") or bytes with wildcards: ``?`` is any
    hex digit (``??`` any byte) and ``*`` any number of bytes, so "0059 ?? 0B00"
    skips one byte and "*0B" finds 0B anywhere. Matching is anchored at the start
    (see ``manufacturer_matches``); the pattern always lines up with whole bytes.
    """
    t = _HEX_SEPARATORS.sub("", text.strip().lower())
    if t.startswith("0x"):
        t = t[2:]
    segments = t.split("*")
    if not any(segments) or any(len(seg) % 2 or any(c not in _PATTERN_CHARS for c in seg)
                                for seg in segments):
        return None
    any_bytes = "(?:[0-9a-f]{2})*"
    return re.compile(any_bytes.join(seg.replace("?", "[0-9a-f]") for seg in segments))


def hex_query(text: str) -> str | None:
    """The text as lowercase hex digits ("5900", "0x5900", "59 00"...), else None."""
    p = byte_pattern(text)
    return p.pattern if p and re.fullmatch("[0-9a-f]+", p.pattern) else None


def manufacturer_matches(manufacturer_data: dict[int, bytes],
                         query: str | re.Pattern[str]) -> bool:
    """Whether some Manufacturer Specific Data starts with the bytes in ``query``.

    ``query`` is a ``byte_pattern`` (or its text). Matches the bytes as sent on air
    (company ID little-endian, e.g. 59 00 ...), and also the way the company ID is
    usually written (0x0059 → "0059...").
    """
    pattern = query if isinstance(query, re.Pattern) else byte_pattern(query)
    if pattern is None:
        return False
    for company_id, payload in manufacturer_data.items():
        on_air = manufacturer_raw(company_id, payload).hex()
        written = f"{company_id:04x}{payload.hex()}"
        if pattern.match(on_air) or pattern.match(written):
            return True
    return False


@dataclass(frozen=True)
class DeviceFilter:
    """Criteria for picking devices out of a scan. Unset fields match anything.

    name     case-insensitive substring of the advertised name (whole name if
             ``exact_name``)
    address  exact address (case-insensitive)
    mfr      manufacturer data starts with these hex bytes; ``?`` and ``*`` are
             wildcards (see ``byte_pattern`` and ``manufacturer_matches``)
    service  advertised service UUID (short "180d" or full)
    min_rssi weakest signal accepted, in dBm
    fields   (name, value) pairs for filters registered by protocol extensions
             (registry.register_filter_field), e.g. (("model", "0x000B"),)
    """

    name: str | None = None
    address: str | None = None
    mfr: str | None = None
    service: str | None = None
    min_rssi: int | None = None
    exact_name: bool = False
    fields: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        known = registry.filter_fields()
        for name, value in self.fields:
            if name not in known:
                raise ValueError(f"Unknown filter {name!r}")
            known[name].parse(str(value))  # raises ValueError with the reason
        if self.mfr is not None and byte_pattern(self.mfr) is None:
            raise ValueError("Manufacturer filter must be hex bytes, e.g. 5900 or 0059??0B"
                             f" (got '{self.mfr}')")

    @property
    def is_empty(self) -> bool:
        return not self.fields and all(v is None for v in (self.name, self.address, self.mfr,
                                                           self.service, self.min_rssi))

    def matches(self, *, address: str, name: str | None, rssi: int,
                manufacturer_data: dict[int, bytes], service_uuids: list[str],
                adv: ParsedAdvertisement | None = None) -> bool:
        if self.address is not None and address.lower() != self.address.lower():
            return False
        if self.name is not None:
            want, have = self.name.lower(), (name or "").lower()
            if (want != have) if self.exact_name else (want not in have):
                return False
        if self.min_rssi is not None and rssi < self.min_rssi:
            return False
        if self.mfr is not None and not manufacturer_matches(manufacturer_data, self.mfr):
            return False
        if self.service is not None:
            want = uuids.normalize(self.service)
            if want not in (uuids.normalize(u) for u in service_uuids):
                return False
        if self.fields:
            known = registry.filter_fields()
            if adv is None:
                return False
            for fname, value in self.fields:
                ff = known[fname]
                if not ff.match(adv, ff.parse(str(value))):
                    return False
        return True


def split_criteria(criteria: dict) -> dict:
    """Keyword criteria with registered filter fields moved into ``fields``."""
    known = registry.filter_fields()
    out = {k: v for k, v in criteria.items() if k not in known}
    extra = tuple((k, str(v)) for k, v in criteria.items() if k in known and v is not None)
    if extra:
        out["fields"] = tuple(out.get("fields", ())) + extra
    return out


_FIELD_TERM = re.compile(r"^([A-Za-z][\w-]*)\s*[:=]\s*(\S+)$")


def field_terms(text: str) -> tuple[tuple[str, str], ...] | None:
    """"model:0B sn:1234" -> (("model", "0B"), ("sn", "1234")) when every term is a
    registered filter with a valid value, else None (plain text search)."""
    known = registry.filter_fields()
    terms = []
    for word in text.split():
        m = _FIELD_TERM.match(word)
        if not m or m.group(1).lower() not in known:
            return None
        name, value = m.group(1).lower(), m.group(2)
        try:
            known[name].parse(value)
        except ValueError:
            return None
        terms.append((name, value))
    return tuple(terms) or None
