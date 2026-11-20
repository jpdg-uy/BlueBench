"""Extension points for protocol-specific decoders (no Qt, no bleak).

A protocol package (a company's own advertising format, for example) plugs in
here instead of changing the core:

* ``register_adv_decoder(fn)``: ``fn(adv)`` gets a ``ParsedAdvertisement`` and
  returns a ``Decoded`` (extra fields, a short label for the device list and,
  optionally, what identifies the device across address changes) or None.
* ``register_names(table)``: extra UUID/company names, same structure as
  ``data/assigned_numbers.json`` (the user's own file still wins).

Extensions are listed by module name in ``bluebench.extensions.MODULES`` and
imported on first use by ``load_extensions()``.
"""
from __future__ import annotations

import importlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .adv_parser import AdvField, ParsedAdvertisement

log = logging.getLogger(__name__)


@dataclass
class Decoded:
    """What a decoder recognised in one advertisement."""

    fields: list[AdvField] = field(default_factory=list)  # added to the advertisement details
    label: str | None = None  # short text for the device list, e.g. "Acme · model 0x000B"
    # Stays the same while the device changes its (private) address, e.g. a serial
    # number sent in clear: rows with the same identity are one device.
    identity: tuple | None = None
    # When there is no identity: what must be equal for a new private address to be
    # taken as the same device that just went quiet (see Scanner).
    similarity: tuple | None = None


AdvDecoder = Callable[["ParsedAdvertisement"], "Decoded | None"]

_adv_decoders: list[AdvDecoder] = []
_names: list[dict[str, dict[str, str]]] = []
_name_listeners: list[Callable[[], None]] = []
_loaded = False


def register_adv_decoder(fn: AdvDecoder) -> AdvDecoder:
    if fn not in _adv_decoders:
        _adv_decoders.append(fn)
    return fn


def register_names(table: dict[str, dict[str, str]]) -> None:
    _names.append(table)
    for fn in _name_listeners:
        fn()


def on_names_changed(fn: Callable[[], None]) -> None:
    _name_listeners.append(fn)


def extra_names() -> list[dict[str, dict[str, str]]]:
    load_extensions()
    return list(_names)


def decode(adv: ParsedAdvertisement) -> list[Decoded]:
    load_extensions()
    out = []
    for fn in _adv_decoders:
        try:
            d = fn(adv)
        except Exception:  # a broken decoder must never stop the scan
            log.exception("Advertising decoder %r failed", fn)
            continue
        if d is not None:
            out.append(d)
    return out


def load_extensions() -> None:
    """Import the modules in ``bluebench.extensions.MODULES`` (once)."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        from .. import extensions
    except ImportError:
        return
    for name in getattr(extensions, "MODULES", ()):
        try:
            importlib.import_module(name)
        except Exception:
            log.exception("Could not load extension %s", name)


def _reset_for_tests() -> Any:
    """Forget registered decoders and names (tests only). Returns a restore function."""
    global _loaded
    saved = (list(_adv_decoders), list(_names), _loaded)
    _adv_decoders.clear()
    _names.clear()
    _loaded = True

    def restore() -> None:
        global _loaded
        _adv_decoders[:] = saved[0]
        _names[:] = saved[1]
        _loaded = saved[2]
    return restore
