"""Resolve private addresses (RPAs) with Identity Resolving Keys.

An RPA is ``prand (24 bits) || hash (24 bits)``, written most significant byte
first: ``AA:BB:CC`` is prand (its top two bits are 01) and ``DD:EE:FF`` the hash.
It belongs to the device whose IRK gives ``ah(IRK, prand) == hash``, where
``ah(k, r) = e(k, 0^104 || r) mod 2^24`` and ``e`` is AES-128 (Core spec,
Vol 3 Part H, 2.2.2). With the IRK of a device, every address it advertises
with can be told apart from look-alikes: no guessing.

Keys live in ``~/.bluebench/irks.json`` as ``[{"label": ..., "irk": "hex"}]``.
An IRK is written most significant byte first, as in the spec; tools that show
it in transmission order (least significant first) are accepted too, because
both byte orders are tried.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..paths import DATA_DIR

log = logging.getLogger(__name__)

USER_FILE = DATA_DIR / "irks.json"

# -- AES-128, encryption of one block (FIPS-197) ---------------------------------
_SBOX = bytes.fromhex(
    "637c777bf26b6fc53001672bfed7ab76ca82c97dfa5947f0add4a2af9ca472c0"
    "b7fd9326363ff7cc34a5e5f171d8311504c723c31896059a071280e2eb27b275"
    "09832c1a1b6e5aa0523bd6b329e32f8453d100ed20fcb15b6acbbe394a4c58cf"
    "d0efaafb434d338545f9027f503c9fa851a3408f929d38f5bcb6da2110fff3d2"
    "cd0c13ec5f974417c4a77e3d645d197360814fdc222a908846eeb814de5e0bdb"
    "e0323a0a4906245cc2d3ac629195e479e7c8376d8dd54ea96c56f4ea657aae08"
    "ba78252e1ca6b4c6e8dd741f4bbd8b8a703eb5664803f60e613557b986c11d9e"
    "e1f8981169d98e949b1e87e9ce5528df8ca1890dbfe6426841992d0fb054bb16")
_RCON = (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36)


def _xtime(b: int) -> int:
    b <<= 1
    return (b ^ 0x11B) if b & 0x100 else b


def _expand(key: bytes) -> list[bytes]:
    w = [key[i:i + 4] for i in range(0, 16, 4)]
    for i in range(4, 44):
        t = w[i - 1]
        if i % 4 == 0:
            t = bytes(_SBOX[b] for b in t[1:] + t[:1])
            t = bytes([t[0] ^ _RCON[i // 4 - 1]]) + t[1:]
        w.append(bytes(a ^ b for a, b in zip(w[i - 4], t)))
    return [b"".join(w[r * 4:r * 4 + 4]) for r in range(11)]


def aes128_encrypt(key: bytes, block: bytes) -> bytes:
    if len(key) != 16 or len(block) != 16:
        raise ValueError("AES-128 needs a 16-byte key and a 16-byte block")
    rk = _expand(key)
    s = bytearray(a ^ b for a, b in zip(block, rk[0]))
    for rnd in range(1, 11):
        s = bytearray(_SBOX[b] for b in s)                                   # SubBytes
        s = bytearray(s[(i + 4 * (i % 4)) % 16] for i in range(16))         # ShiftRows
        if rnd < 10:                                                         # MixColumns
            for c in range(0, 16, 4):
                a = s[c:c + 4]
                t = a[0] ^ a[1] ^ a[2] ^ a[3]
                s[c:c + 4] = bytes(a[i] ^ t ^ _xtime(a[i] ^ a[(i + 1) % 4]) for i in range(4))
        s = bytearray(a ^ b for a, b in zip(s, rk[rnd]))                     # AddRoundKey
    return bytes(s)


# -- RPA ------------------------------------------------------------------------
def ah(irk: bytes, prand: bytes) -> bytes:
    """The random address hash function: 3 bytes, most significant first."""
    return aes128_encrypt(irk, bytes(13) + prand)[-3:]


def address_bytes(address: str) -> bytes | None:
    t = address.replace(":", "").replace("-", "")
    try:
        b = bytes.fromhex(t)
    except ValueError:
        return None
    return b if len(b) == 6 else None


def is_rpa(address: str) -> bool:
    b = address_bytes(address)
    return b is not None and b[0] >> 6 == 0b01


def resolves(irk: bytes, address: str) -> bool:
    """Whether ``address`` is a private address made with ``irk``."""
    b = address_bytes(address)
    if b is None or b[0] >> 6 != 0b01:
        return False
    return ah(irk, b[:3]) == b[3:]


def parse_irk(text: str) -> bytes:
    t = re.sub(r"[\s:\-,]", "", text.strip())
    t = t[2:] if t.lower().startswith("0x") else t
    try:
        b = bytes.fromhex(t)
    except ValueError:
        raise ValueError(f"an IRK is 16 bytes in hex (32 digits), got {text!r}") from None
    if len(b) != 16:
        raise ValueError(f"an IRK is 16 bytes (32 hex digits), got {len(b)} bytes")
    return b


# -- the user's keys -------------------------------------------------------------
@dataclass(frozen=True)
class IdentityKey:
    label: str
    irk: bytes

    def matches(self, address: str) -> bool:
        return resolves(self.irk, address) or resolves(self.irk[::-1], address)


class KeyRing:
    """The IRKs to resolve with. ``resolve`` caches per address."""

    def __init__(self, keys: list[IdentityKey] | None = None, path: Path | None = None) -> None:
        self.path = path
        self.keys: list[IdentityKey] = list(keys or [])
        self._cache: dict[str, IdentityKey | None] = {}
        self.listeners: list[Callable[[], None]] = []

    @classmethod
    def load(cls, path: Path = USER_FILE) -> "KeyRing":
        ring = cls(path=path)
        ring.reload()
        return ring

    def reload(self) -> None:
        keys: list[IdentityKey] = []
        if self.path is not None and self.path.exists():
            try:
                for item in json.loads(self.path.read_text(encoding="utf-8")):
                    keys.append(IdentityKey(str(item["label"]), parse_irk(item["irk"])))
            except (OSError, ValueError, KeyError, TypeError) as exc:
                log.warning("Ignoring %s: %s", self.path, exc)
        self._set(keys)

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(
            [{"label": k.label, "irk": k.irk.hex().upper()} for k in self.keys], indent=2),
            encoding="utf-8")

    def _set(self, keys: list[IdentityKey]) -> None:
        self.keys = keys
        self._cache.clear()
        for fn in list(self.listeners):
            fn()

    def add(self, label: str, irk: str | bytes) -> IdentityKey:
        label = label.strip()
        if not label:
            raise ValueError("give the key a label")
        key = IdentityKey(label, irk if isinstance(irk, bytes) else parse_irk(irk))
        self._set([k for k in self.keys if k.label != label] + [key])
        return key

    def remove(self, label: str) -> bool:
        keys = [k for k in self.keys if k.label != label]
        found = len(keys) != len(self.keys)
        self._set(keys)
        return found

    def resolve(self, address: str) -> IdentityKey | None:
        """The key that made this private address, if any."""
        if not self.keys or not is_rpa(address):
            return None
        a = address.upper()
        if a not in self._cache:
            if len(self._cache) > 20000:
                self._cache.clear()
            self._cache[a] = next((k for k in self.keys if k.matches(a)), None)
        return self._cache[a]


_default: KeyRing | None = None


def keyring() -> KeyRing:
    """The user's key ring, loaded once (shared by the app, the CLI and the API)."""
    global _default
    if _default is None:
        _default = KeyRing.load()
    return _default


def make_rpa(irk: bytes, prand: bytes) -> str:
    """An RPA for ``irk`` (tests and examples); prand's top two bits are forced to 01."""
    p = bytes([(prand[0] & 0x3F) | 0x40]) + prand[1:3]
    return ":".join(f"{b:02X}" for b in p + ah(irk, p))


# -- filter: irk:LABEL in the scanner box, --irk LABEL in the CLI, irk= in the API ----
def _match_irk(adv, label: str) -> bool:
    ident = getattr(adv, "identity", None)
    return bool(ident) and ident[0] == "irk" and ident[1].lower() == label.lower()


def _register_filter() -> None:
    from . import registry
    registry.register_filter_field(registry.FilterField(
        "irk", "resolved with this IRK of the key ring (its label)", str, _match_irk, "LABEL"))


_register_filter()
