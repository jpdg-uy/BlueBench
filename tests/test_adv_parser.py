import struct
import uuid

from bluebench.ble.adv_parser import decode_eddystone, parse_advertisement


def labels(fields):
    return [f.label for f in fields]


def test_basic_fields():
    p = parse_advertisement("Thingy", {0x0059: b"\x01\x02"}, {}, ["180f"], -4)
    assert p.local_name == "Thingy"
    assert labels(p.fields) == ["Complete Local Name", "TX Power Level",
                                "Service UUIDs", "Manufacturer Data"]
    mfr = p.fields[3]
    assert mfr.value == "Nordic Semiconductor <0x0059>"
    assert mfr.children[0].value == "0x0102"
    svc = p.fields[2].children[0]
    assert (svc.label, svc.value) == ("180F", "Battery")
    assert p.beacon is None


def test_ibeacon():
    prox = uuid.UUID("e2c56db5-dffb-48d2-b060-d0f5a71096e0")
    payload = b"\x02\x15" + prox.bytes + struct.pack(">HHb", 1, 2, -59)
    p = parse_advertisement(None, {0x004C: payload}, {}, [], None)
    assert p.beacon == "iBeacon"
    values = {c.label: c.value for c in p.fields[0].children}
    assert values["Proximity UUID"] == str(prox).upper()
    assert values["Major"] == "1" and values["Minor"] == "2"
    assert values["Measured Power"] == "-59 dBm @ 1 m"


def test_apple_non_ibeacon_is_not_beacon():
    p = parse_advertisement(None, {0x004C: b"\x10\x05\x01"}, {}, [], None)
    assert p.beacon is None


def test_eddystone_url():
    kind, fields = decode_eddystone(b"\x10\xee\x03nordicsemi\x00")
    assert kind == "Eddystone-URL"
    assert {f.label: f.value for f in fields}["URL"] == "https://nordicsemi.com/"


def test_eddystone_uid_and_tlm():
    kind, _ = decode_eddystone(b"\x00\xec" + bytes(range(16)))
    assert kind == "Eddystone-UID"
    tlm = b"\x20\x00" + struct.pack(">HhII", 3000, 23 * 256, 10, 600)
    kind, fields = decode_eddystone(tlm)
    values = {f.label: f.value for f in fields}
    assert kind == "Eddystone-TLM"
    assert values["Battery"] == "3000 mV"
    assert values["Temperature"] == "23.00 °C"
    assert values["Uptime"] == "60.0 s"


def test_eddystone_via_service_data():
    p = parse_advertisement(None, {}, {"0000feaa-0000-1000-8000-00805f9b34fb": b"\x10\x00\x02x.y"}, [], None)
    assert p.beacon == "Eddystone-URL"


def test_garbage_does_not_crash():
    assert decode_eddystone(b"") == (None, [])
    assert decode_eddystone(b"\x10\x00\x09") == (None, [])
    assert decode_eddystone(b"\x00\x00") == (None, [])
