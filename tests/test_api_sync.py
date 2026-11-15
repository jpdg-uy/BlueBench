"""The blocking API (bluebench.api.sync): same behaviour, from plain functions."""
import threading

import pytest

from bluebench import api
from bluebench.api import sync as bb
from bluebench.ble import connection, pairing, scanner
from bluebench.core.macros import Macro, Step
from fakes import FakeClient, FakeScanner, advert

SENSOR = "C4:3A:00:00:00:01"


@pytest.fixture(autouse=True)
def fake_ble(monkeypatch):
    FakeClient.instances.clear()
    FakeClient.unpaired.clear()
    FakeClient.fail_connect = FakeClient.fail_pair = False
    FakeScanner.fail = None
    FakeScanner.adverts = [advert(SENSOR, "Sensor Demo", -50, mfr={0x0059: b"\xaa\xbb"})]
    monkeypatch.setattr(scanner, "BleakScanner", FakeScanner)
    monkeypatch.setattr(connection, "BleakClient", FakeClient)
    monkeypatch.setattr(pairing, "BleakClient", FakeClient)
    monkeypatch.setattr(pairing, "_is_winrt", lambda: False)


def test_scan_find_connect_read_write():
    assert [d.address for d in bb.scan(0.1, mfr="5900")] == [SENSOR]
    assert bb.find(1, name="sensor").address == SENSOR
    with bb.connect(mfr="5900") as dev:
        assert dev.is_connected and dev.mtu == 247 and "SyncDevice" in repr(dev)
        assert dev.read("2a00") == b"Fake HRM"
        dev.write("2a00", b"x")
        assert dev.services()[0].name == "Generic Access"
        assert dev.pair() == "unknown" and dev.bonded
    assert not dev.is_connected


def test_notifications_and_callbacks():
    with bb.connect(SENSOR) as dev:
        c = FakeClient.instances[-1]
        loop = bb._get_loop()
        with dev.notifications("2a37") as n:
            loop.call_soon_threadsafe(c.push_notification, 12, b"\x07")
            assert n.get(timeout=1) == b"\x07"
            with pytest.raises(TimeoutError):
                n.get(timeout=0.05)
        got, threads = [], []
        dev.subscribe("2a37", lambda v: (got.append(v), threads.append(threading.current_thread().name)))
        loop.call_soon_threadsafe(c.push_notification, 12, b"\x08")
        dev.read("2a00")   # a round trip through the loop, so the notification has run
        dev.unsubscribe("2a37")
        assert got == [b"\x08"] and threads == ["bluebench-api"]


def test_uart_and_macro():
    with bb.connect(SENSOR) as dev:
        with dev.uart() as u:
            u.send("ping\n")
            assert u.readline(timeout=1) == b"ping\n"
        m = Macro("m", [Step("read", "2a00", "46 61 6B 65 ?? ?? ?? ??")])
        assert dev.run_macro(m).ok
        bad = dev.run_macro(Macro("bad", [Step("read", "2a00", "FF")]))
        assert bad.steps[0].error == "read 46-61-6B-65-20-48-52-4D, expected FF"


def test_errors_are_the_same():
    with pytest.raises(api.DeviceNotFound):
        bb.find(0.1, mfr="ffff")
    with bb.connect(SENSOR) as dev:
        with pytest.raises(api.GattError):
            dev.read("2a99")
    with pytest.raises(api.NotConnected):
        dev.read("2a00")
