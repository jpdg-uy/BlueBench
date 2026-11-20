"""Extension points for protocol-specific decoders (no Qt, no bleak).

A protocol package (a company's own advertising format, for example) plugs in
here instead of changing the core:

* ``register_adv_decoder(fn)``: ``fn(adv)`` gets a ``ParsedAdvertisement`` and
  returns a ``Decoded`` (extra fields, a short label for the device list and,
  optionally, what identifies the device across address changes) or None.
* ``register_names(table)``: extra UUID/company names, same structure as
  ``data/assigned_numbers.json`` (the user's own file still wins).
* ``register_filter_field(FilterField(...))``: a device filter on decoded data,
  offered as ``--NAME`` by the CLI, ``NAME=`` by the API and ``NAME:VALUE`` in the
  scanner's filter box.
* ``register_device_page(DevicePage(...))``: a tab in the device view, added once
  the services are discovered when ``applies(session)`` is true.
* ``register_cli(fn)``: ``fn(command, cli_module)`` adds CLI subcommands.
* ``register_value_decoder(uuids, fn)``: ``fn(value)`` explains a characteristic
  value (list of ``AdvField``) in the traffic views.

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
    from .adv_parser import AdvField, ParsedAdvertisement  # noqa: F401

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


@dataclass(frozen=True)
class FilterField:
    """A filter criterion that needs decoded advertising data."""

    name: str                                         # "model": --model, model=, model:
    help: str                                         # one line, for the CLI
    parse: Callable[[str], Any]                       # text -> value; ValueError if bad
    match: Callable[["ParsedAdvertisement", Any], bool]
    metavar: str = "VALUE"


@dataclass(frozen=True)
class DevicePage:
    title: str
    applies: Callable[[Any], bool]          # (DeviceSession) -> bool
    factory: Callable[[Any, Any], Any]      # (DeviceSession, parent widget) -> QWidget


AdvDecoder = Callable[["ParsedAdvertisement"], "Decoded | None"]

_adv_decoders: list[AdvDecoder] = []
_filter_fields: dict[str, FilterField] = {}
_device_pages: list[DevicePage] = []
_cli: list[Callable[..., None]] = []
_value_decoders: dict[str, list[Callable[[bytes], list[AdvField]]]] = {}
_names: list[dict[str, dict[str, str]]] = []
_name_listeners: list[Callable[[], None]] = []
_loaded = False


def register_adv_decoder(fn: AdvDecoder) -> AdvDecoder:
    if fn not in _adv_decoders:
        _adv_decoders.append(fn)
    return fn


def register_filter_field(ff: FilterField) -> FilterField:
    _filter_fields[ff.name] = ff
    return ff


def filter_fields() -> dict[str, FilterField]:
    load_extensions()
    return dict(_filter_fields)


def register_device_page(page: DevicePage) -> DevicePage:
    _device_pages.append(page)
    return page


def device_pages() -> list[DevicePage]:
    load_extensions()
    return list(_device_pages)


def register_cli(fn: Callable[..., None]) -> Callable[..., None]:
    _cli.append(fn)
    return fn


def cli_extensions() -> list[Callable[..., None]]:
    load_extensions()
    return list(_cli)


def register_value_decoder(uuid_list: list[str], fn: Callable[[bytes], list[AdvField]]) -> None:
    from . import uuids
    for u in uuid_list:
        _value_decoders.setdefault(uuids.normalize(u), []).append(fn)


def decode_value(uuid: str, value: bytes) -> list[AdvField]:
    """What registered decoders make of a characteristic value (empty if nothing)."""
    from . import uuids
    load_extensions()
    out: list[AdvField] = []
    for fn in _value_decoders.get(uuids.normalize(uuid), []):
        try:
            out += fn(bytes(value))
        except Exception:
            log.exception("Value decoder %r failed", fn)
    return out


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
    saved = (list(_adv_decoders), list(_names), _loaded, dict(_filter_fields),
             list(_device_pages), list(_cli), dict(_value_decoders))
    _adv_decoders.clear()
    _names.clear()
    _filter_fields.clear()
    _device_pages.clear()
    _cli.clear()
    _value_decoders.clear()
    _loaded = True

    def restore() -> None:
        global _loaded
        _adv_decoders[:] = saved[0]
        _names[:] = saved[1]
        _loaded = saved[2]
        _filter_fields.clear()
        _filter_fields.update(saved[3])
        _device_pages[:] = saved[4]
        _cli[:] = saved[5]
        _value_decoders.clear()
        _value_decoders.update(saved[6])
    return restore
