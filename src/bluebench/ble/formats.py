"""Value codecs used to display characteristic values and parse user input."""
from __future__ import annotations

import struct
from enum import Enum


class ValueFormat(str, Enum):
    HEX = "Hex"
    UTF8 = "UTF-8"
    UINT8 = "UInt8"
    UINT16_LE = "UInt16 LE"
    UINT16_BE = "UInt16 BE"
    UINT32_LE = "UInt32 LE"
    UINT32_BE = "UInt32 BE"
    SINT8 = "SInt8"
    SINT16_LE = "SInt16 LE"
    SINT32_LE = "SInt32 LE"
    FLOAT32_LE = "Float32 LE"
    BOOL = "Bool"


_STRUCT = {
    ValueFormat.UINT8: "<B",
    ValueFormat.UINT16_LE: "<H",
    ValueFormat.UINT16_BE: ">H",
    ValueFormat.UINT32_LE: "<I",
    ValueFormat.UINT32_BE: ">I",
    ValueFormat.SINT8: "<b",
    ValueFormat.SINT16_LE: "<h",
    ValueFormat.SINT32_LE: "<i",
    ValueFormat.FLOAT32_LE: "<f",
}


def to_hex(data: bytes, sep: str = "-") -> str:
    return sep.join(f"{b:02X}" for b in data)


def decode(data: bytes, fmt: ValueFormat) -> str:
    """Render bytes in the given format. Never raises: falls back to a note."""
    if fmt is ValueFormat.HEX:
        return "0x" + to_hex(data) if data else "(empty)"
    if fmt is ValueFormat.UTF8:
        return data.decode("utf-8", errors="replace")
    if fmt is ValueFormat.BOOL:
        return "(empty)" if not data else ("true" if any(data) else "false")
    code = _STRUCT[fmt]
    size = struct.calcsize(code)
    if len(data) != size:
        return f"(expects {size} byte{'s' if size > 1 else ''}, got {len(data)})"
    value = struct.unpack(code, data)[0]
    return f"{value:g}" if isinstance(value, float) else str(value)


def encode(text: str, fmt: ValueFormat) -> bytes:
    """Parse user input into bytes. Raises ValueError with a readable message."""
    text = text.strip() if fmt is not ValueFormat.UTF8 else text
    if fmt is ValueFormat.HEX:
        cleaned = text.lower().removeprefix("0x")
        for ch in " -:,":
            cleaned = cleaned.replace(ch, "")
        if len(cleaned) % 2:
            raise ValueError("Hex input needs an even number of digits")
        try:
            return bytes.fromhex(cleaned)
        except ValueError:
            raise ValueError("Hex input contains non-hex characters") from None
    if fmt is ValueFormat.UTF8:
        return text.encode("utf-8")
    if fmt is ValueFormat.BOOL:
        low = text.lower()
        if low in ("1", "true", "on", "yes"):
            return b"\x01"
        if low in ("0", "false", "off", "no"):
            return b"\x00"
        raise ValueError("Bool input must be true/false or 1/0")
    code = _STRUCT[fmt]
    try:
        value: float | int = float(text) if fmt is ValueFormat.FLOAT32_LE else int(text, 0)
    except ValueError:
        raise ValueError(f"'{text}' is not a valid {fmt.value} number") from None
    try:
        return struct.pack(code, value)
    except struct.error:
        raise ValueError(f"{text} is out of range for {fmt.value}") from None
