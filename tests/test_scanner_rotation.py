"""Scanner: one entry per device while it changes its private address."""
from types import SimpleNamespace

import pytest
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from bluebench.ble import registry, scanner as scanner_mod
from bluebench.ble.registry import Decoded
from bluebench.ble.scanner import Scanner, address_kind, address_type_of

# Resolvable private addresses: the two top bits are 01 (first byte 0x40..0x7F).
RPA1, RPA2, RPA3 = "4A:00:00:00:00:01", "5B:00:00:00:00:02", "6C:00:00:00:00:03"
MFR = {0x0059: b"\x01\x0b\x00"}


def winrt_details(kind: str):
    args = SimpleNamespace(bluetooth_address_type=SimpleNamespace(name=kind))
    return SimpleNamespace(adv=args, scan=None)


@pytest.fixture
def clock(monkeypatch):
    t = SimpleNamespace(now=100.0)
    monkeypatch.setattr(scanner_mod.time, "monotonic", lambda: t.now)
    return t


@pytest.fixture
def sc(qapp, clock):
    s = Scanner()
    yield s
    s.deleteLater()


def hear(s, address, name="Sensor", rssi=-60, mfr=MFR, kind="RANDOM"):
    s._on_detection(BLEDevice(address, name, winrt_details(kind)),
                    AdvertisementData(name, mfr, {}, [], None, rssi, ()))


@pytest.mark.parametrize("address, typ, kind", [
    ("C4:00:00:00:00:01", "random", "static"),
    ("4A:00:00:00:00:01", "random", "rpa"),
    ("0A:00:00:00:00:01", "random", "nrpa"),
    ("4A:00:00:00:00:01", "public", "public"),
    ("4A:00:00:00:00:01", None, None),
])
def test_address_kind(address, typ, kind):
    assert address_kind(address, typ) == kind


def test_address_type_from_backend_details():
    assert address_type_of(BLEDevice("x", None, winrt_details("PUBLIC"))) == "public"
    assert address_type_of(BLEDevice("x", None, {"props": {"AddressType": "random"}})) == "random"
    assert address_type_of(BLEDevice("x", None, None)) is None


def test_rotating_rpa_stays_one_entry(sc, clock):
    keys = []
    sc.device_updated.connect(keys.append)
    hear(sc, RPA1)
    clock.now += 1.0
    hear(sc, RPA2)                 # the old address went quiet, a new one appears
    clock.now += 1.0
    hear(sc, RPA3)
    assert list(sc.devices) == [RPA1]
    e = sc.devices[RPA1]
    assert e.address == RPA3 and list(e.addresses) == [RPA1, RPA2, RPA3]
    assert e.joined == "guess" and e.adv_count == 3 and e.address_kind == "rpa"
    assert keys == [RPA1] * 3
    assert sc.entry_for(RPA2) is e and sc.entry_for(RPA3.lower()) is e


@pytest.mark.parametrize("change", [
    dict(name="Other"),                      # another name
    dict(mfr={0x0059: b"\x01"}),             # another kind of data
    dict(rssi=-95),                          # much weaker: somewhere else
    dict(kind="PUBLIC"),                     # not a private address
])
def test_different_devices_are_not_joined(sc, clock, change):
    hear(sc, RPA1)
    clock.now += 1.0
    hear(sc, RPA2, **change)
    assert len(sc.devices) == 2


def test_no_join_after_the_window_or_when_disabled(sc, clock):
    hear(sc, RPA1)
    clock.now += scanner_mod.ROTATION_WINDOW_S + 1
    hear(sc, RPA2)
    assert len(sc.devices) == 2
    sc.clear()
    sc.follow_rotation = False
    hear(sc, RPA1)
    clock.now += 1.0
    hear(sc, RPA2)
    assert len(sc.devices) == 2


def test_guess_is_undone_when_both_addresses_keep_advertising(sc, clock):
    hear(sc, RPA1)
    clock.now += 1.0
    hear(sc, RPA2)                 # joined by guess...
    clock.now += 0.2
    hear(sc, RPA1)                 # ... a straggler right after the switch: still one
    assert len(sc.devices) == 1
    clock.now += 1.0
    hear(sc, RPA2)
    clock.now += 0.1
    hear(sc, RPA1)                 # both alive: two devices after all
    assert set(sc.devices) == {RPA1, RPA2}
    assert sc.devices[RPA1].address == RPA1 and list(sc.devices[RPA1].addresses) == [RPA1]
    assert sc.devices[RPA1].joined is None
    assert sc.entry_for(RPA2) is sc.devices[RPA2]
    clock.now += 0.1
    hear(sc, RPA2)
    assert sc.devices[RPA2].adv_count >= 2 and len(sc.devices) == 2


def test_decoder_identity_joins_exactly(sc, clock):
    def decoder(adv):
        p = adv.manufacturer_data.get(0x0059)
        return Decoded(identity=("demo", p[0])) if p else None
    restore = registry._reset_for_tests()
    registry.register_adv_decoder(decoder)
    try:
        hear(sc, RPA1, mfr={0x0059: b"\x07"}, rssi=-40)
        clock.now += 1.0
        hear(sc, RPA2, mfr={0x0059: b"\x08"}, rssi=-40)  # another serial: another device
        clock.now += 100                                   # long after, much weaker
        hear(sc, RPA3, mfr={0x0059: b"\x07"}, rssi=-90)
        assert set(sc.devices) == {RPA1, RPA2}
        e = sc.devices[RPA1]
        assert e.joined == "identity" and e.address == RPA3
        clock.now += 0.1
        hear(sc, RPA1, mfr={0x0059: b"\x07"})             # identity joins are never undone
        assert set(sc.devices) == {RPA1, RPA2}
    finally:
        restore()


def test_windows_lost_events_are_not_packets(sc, clock):
    hear(sc, RPA1)
    clock.now += 60
    hear(sc, RPA1, rssi=-127)             # Windows: "not heard for a minute"
    e = sc.entry_for(RPA1)
    assert e.last_seen == 100.0 and e.rssi == -60 and e.adv_count == 1


def test_look_alike_devices_are_not_guessed(sc, clock):
    hear(sc, RPA1)
    hear(sc, "7D:00:00:00:00:04")          # an identical second device
    clock.now += 5
    hear(sc, RPA2)                         # which one rotated? no way to tell
    assert sc.entry_for(RPA2).addresses.keys() == {RPA2}


def test_a_device_still_advertising_is_not_continued(sc, clock):
    for _ in range(5):                     # advertises every 2 s
        hear(sc, RPA1)
        clock.now += 2
    clock.now -= 1.5                       # 0.5 s after its last packet: not quiet
    hear(sc, RPA2)
    assert sc.entry_for(RPA2).addresses.keys() == {RPA2}


def test_a_name_heard_later_undoes_a_wrong_guess(sc, clock):
    hear(sc, RPA1, name="Alpha")
    clock.now += 5
    hear(sc, RPA2, name=None)              # first packet: no name yet, joined
    assert sc.entry_for(RPA2).key == RPA1
    clock.now += 0.2
    hear(sc, RPA2, name="Bravo")           # scan response: another device
    a, b = sc.entry_for(RPA1), sc.entry_for(RPA2)
    assert a is not b and a.name == "Alpha" and b.name == "Bravo"
    assert a.addresses.keys() == {RPA1} and a.address == RPA1 and a.joined is None


def test_irk_resolution_joins_exactly_among_look_alikes(sc, clock):
    from bluebench.ble import irk
    key = bytes(range(16))
    sc.keys = irk.KeyRing([irk.IdentityKey("Board 7", key)])
    a1, a2 = irk.make_rpa(key, b"\x01\x02\x03"), irk.make_rpa(key, b"\x04\x05\x06")
    hear(sc, a1)
    hear(sc, "7D:00:00:00:00:04")          # a look-alike, still advertising
    clock.now += 0.1                       # the new address right away, and not quiet
    hear(sc, a2, rssi=-90)                 # even with a very different signal
    e = sc.entry_for(a2)
    assert e is sc.entry_for(a1) and e.joined == "identity" and e.resolved_by == "Board 7"
    assert e.adv.identity == ("irk", "Board 7")
    assert sc.entry_for("7D:00:00:00:00:04") is not e
