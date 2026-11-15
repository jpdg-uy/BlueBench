import pytest
import shiboken6

from bluebench.ble import connection
from bluebench.ble.connection import DeviceSession
from fakes import FakeClient


@pytest.fixture
def session(qapp, monkeypatch):
    FakeClient.instances.clear()
    FakeClient.fail_connect = False
    monkeypatch.setattr(connection, "BleakClient", FakeClient)
    s = DeviceSession("AA:BB:CC:DD:EE:FF", "Fake")
    s.events = []
    s.state_changed.connect(lambda st: s.events.append(("state", st)))
    s.value_updated.connect(lambda h, v, src: s.events.append(("value", h, v, src)))
    s.log.connect(lambda lvl, msg: s.events.append(("log", lvl, msg)))
    yield s
    shiboken6.delete(s)


async def test_connect_and_discover(session):
    assert await session.connect()
    assert session.is_connected
    assert [e[1] for e in session.events if e[0] == "state"] == ["connecting", "discovering", "connected"]
    assert len(session.services) == 3
    assert session.mtu == 247
    assert session.find_characteristic("6E400003-B5A3-F393-E0A9-E50E24DCCA9E").handle == 24


async def test_connect_failure_logs_error(session):
    FakeClient.fail_connect = True
    assert not await session.connect()
    assert session.state == "disconnected"
    assert any(e[0] == "log" and e[1] == "error" and "Device not found" in e[2] for e in session.events)


async def test_read_write(session):
    await session.connect()
    assert await session.read(3) == b"Fake HRM"
    assert await session.write(3, b"New", response=True)
    assert FakeClient.instances[-1].writes == [(3, b"New", True)]
    assert ("value", 3, b"New", "write") in session.events
    assert await session.read(999) is None  # unknown handle -> logged, no crash


async def test_write_error_is_reported(session):
    await session.connect()
    assert not await session.write(15, b"\x02")  # read-only char
    assert any(e[0] == "log" and e[1] == "error" and "Write" in e[2] for e in session.events)


async def test_notifications_and_listeners(session):
    await session.connect()
    got = []
    session.add_notify_listener(12, lambda h, v: got.append(v))
    assert await session.set_notify(12, True)
    assert 12 in session.notifying
    FakeClient.instances[-1].push_notification(12, b"\x00\x48")
    assert got == [b"\x00\x48"]
    assert ("value", 12, b"\x00\x48", "notify") in session.events
    assert await session.set_notify(12, False)
    assert 12 not in session.notifying


async def test_unexpected_disconnect(session):
    await session.connect()
    await session.set_notify(12, True)
    FakeClient.instances[-1].drop_link()
    assert session.state == "disconnected"
    assert not session.notifying
    assert any(e[0] == "log" and e[1] == "warning" and "unexpectedly" in e[2] for e in session.events)
    assert await session.read(3) is None


async def test_reconnect_uses_fresh_client(session):
    await session.connect()
    await session.disconnect()
    assert any(e[0] == "log" and e[2] == "Disconnected" for e in session.events)
    await session.connect()
    assert len(FakeClient.instances) == 2 and session.is_connected
