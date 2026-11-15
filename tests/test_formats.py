import pytest

from bluebench.ble.formats import ValueFormat as F, decode, encode


@pytest.mark.parametrize("fmt,text,raw", [
    (F.HEX, "01 02-ff", b"\x01\x02\xff"),
    (F.HEX, "0xABCD", b"\xab\xcd"),
    (F.UTF8, "hi ", b"hi "),
    (F.UINT8, "255", b"\xff"),
    (F.UINT16_LE, "0x1234", b"\x34\x12"),
    (F.UINT16_BE, "4660", b"\x12\x34"),
    (F.SINT8, "-1", b"\xff"),
    (F.SINT16_LE, "-2", b"\xfe\xff"),
    (F.UINT32_LE, "1", b"\x01\x00\x00\x00"),
    (F.BOOL, "true", b"\x01"),
])
def test_encode(fmt, text, raw):
    assert encode(text, fmt) == raw


@pytest.mark.parametrize("fmt,text", [
    (F.HEX, "abc"), (F.HEX, "zz"), (F.UINT8, "256"), (F.UINT8, "x"), (F.BOOL, "maybe"),
])
def test_encode_errors(fmt, text):
    with pytest.raises(ValueError):
        encode(text, fmt)


def test_decode():
    assert decode(b"\x01\xab", F.HEX) == "0x01-AB"
    assert decode(b"", F.HEX) == "(empty)"
    assert decode(b"\x34\x12", F.UINT16_LE) == "4660"
    assert decode(b"\xff", F.SINT8) == "-1"
    assert decode(b"\x00\x00\x80\x3f", F.FLOAT32_LE) == "1"
    assert decode(b"\x01", F.UINT16_LE).startswith("(expects 2 bytes")
    assert decode("héllo".encode(), F.UTF8) == "héllo"


def test_float_roundtrip():
    assert decode(encode("3.5", F.FLOAT32_LE), F.FLOAT32_LE) == "3.5"
