"""UUID normalisation and human-readable names for services, characteristics,
descriptors and company identifiers.

Names come from a bundled, trimmed copy of the Bluetooth SIG assigned numbers
plus Nordic's well-known vendor UUIDs. Users can add their own names in
``~/.bluebench/uuids.json`` (same structure as the bundled file).
"""
from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from typing import Literal

from ..paths import DATA_DIR
from . import registry

Kind = Literal["services", "characteristics", "descriptors"]

BASE_UUID_SUFFIX = "-0000-1000-8000-00805f9b34fb"
USER_FILE = DATA_DIR / "uuids.json"


def normalize(uuid: str | int) -> str:
    """Return the full lowercase 128-bit form of a 16/32/128-bit UUID."""
    if isinstance(uuid, int):
        return f"{uuid:08x}{BASE_UUID_SUFFIX}"
    u = uuid.strip().lower()
    if u.startswith("0x"):
        u = u[2:]
    if len(u) == 4:
        return f"0000{u}{BASE_UUID_SUFFIX}"
    if len(u) == 8:
        return f"{u}{BASE_UUID_SUFFIX}"
    if len(u) == 32 and "-" not in u:
        return f"{u[:8]}-{u[8:12]}-{u[12:16]}-{u[16:20]}-{u[20:]}"
    return u


def short_form(uuid: str) -> str:
    """'0000180d-0000-1000-8000-00805f9b34fb' -> '180D'; vendor UUIDs unchanged."""
    full = normalize(uuid)
    if full.endswith(BASE_UUID_SUFFIX):
        head = full[:8]
        return (head[4:] if head.startswith("0000") else head).upper()
    return full.upper()


def _merge(dst: dict, src: dict) -> None:
    for section, entries in src.items():
        dst.setdefault(section, {}).update({k.lower(): v for k, v in entries.items()})


@lru_cache(maxsize=1)
def _tables() -> dict[str, dict[str, str]]:
    raw = json.loads(
        resources.files("bluebench.data").joinpath("assigned_numbers.json").read_text("utf-8")
    )
    tables: dict[str, dict[str, str]] = {}
    _merge(tables, raw)
    for extra in registry.extra_names():  # protocol extensions
        _merge(tables, extra)
    if USER_FILE.exists():
        try:
            _merge(tables, json.loads(USER_FILE.read_text("utf-8")))
        except (OSError, ValueError):
            pass  # a broken user file must never stop the app
    # Index UUID sections by full 128-bit form.
    for kind in ("services", "characteristics", "descriptors"):
        tables[kind] = {normalize(k): v for k, v in tables.get(kind, {}).items()}
    tables["companies"] = {
        int(k, 16): v for k, v in tables.get("companies", {}).items()  # type: ignore[misc]
    }
    return tables


def name_for(uuid: str, kind: Kind) -> str | None:
    return _tables()[kind].get(normalize(uuid))


def display_name(uuid: str, kind: Kind) -> str:
    """Known name, or a generic label for unknown UUIDs."""
    name = name_for(uuid, kind)
    if name:
        return name
    return {"services": "Unknown Service", "characteristics": "Unknown Characteristic",
            "descriptors": "Unknown Descriptor"}[kind]


def company_name(company_id: int) -> str:
    return _tables()["companies"].get(company_id, f"Unknown (0x{company_id:04X})")  # type: ignore[call-overload]


def reload() -> None:
    """Re-read the tables, e.g. after the user edited their custom names file."""
    _tables.cache_clear()


registry.on_names_changed(reload)
