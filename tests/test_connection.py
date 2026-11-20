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


async def test_connect_uses_the_newest_address(session):
    from bleak.backends.device import BLEDevice
    newest = BLEDevice("5B:00:00:00:00:02", "Fake", None)
    session.latest_device = lambda: newest
    assert await session.connect()
    assert FakeClient.instances[-1].device is newest and session.address == newest.address


async def test_connect_follows_an_address_change_mid_connect(session, monkeypatch):
    import asyncio
    from bleak.backends.device import BLEDevice
    monkeypatch.setattr(connection, "FOLLOW_POLL_S", 0.01)
    first, second = BLEDevice("4A:00:00:00:00:01", "F", None), BLEDevice("5B:00:00:00:00:02", "F", None)
    current = [first]
    session.latest_device = lambda: current[0]

    class Slow(FakeClient):
        async def connect(self):
            if self.device is first:       # never answers on the old address
                current[0] = second          # ... because the device moved
                await asyncio.sleep(10)
            return await super().connect()
    monkeypatch.setattr(connection, "BleakClient", Slow)
    assert await asyncio.wait_for(session.connect(), 2)
    assert session.address == second.address and FakeClient.instances[-1].device is second
    assert any("Address changed while connecting" in e[2] for e in session.events if e[0] == "log")


async def test_connect_falls_back_to_the_identity_address(session, monkeypatch):
    class OnlyIdentity(FakeClient):
        async def connect(self):
            if self.device.address != "C4:00:00:00:00:99":
                raise OSError("Device not found")
            return await super().connect()
    monkeypatch.setattr(connection, "BleakClient", OnlyIdentity)
    from bleak.backends.device import BLEDevice
    session.device = BLEDevice("4A:00:00:00:00:01", "Fake", None)
    session.identity_address = "C4:00:00:00:00:99"
    assert await session.connect()
    assert any("identity address" in e[2] for e in session.events if e[0] == "log")
