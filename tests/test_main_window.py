import asyncio
from types import SimpleNamespace

import pytest

from bluebench.ble import connection
from bluebench.ui.device_view import DeviceView
from bluebench.ui.main_window import MainWindow
from fakes import FakeClient


@pytest.fixture
def window(qapp, monkeypatch):
    FakeClient.instances.clear()
    FakeClient.fail_connect = False
    monkeypatch.setattr(connection, "BleakClient", FakeClient)
    w = MainWindow()
    yield w
    w.deleteLater()


def advertise(window, address, name):
    adv = SimpleNamespace(local_name=name, rssi=-50, manufacturer_data={}, service_data={},
                          service_uuids=[], tx_power=None)
    window.scanner._on_detection(SimpleNamespace(address=address, name=name), adv)


async def settle():
    for _ in range(10):
        await asyncio.sleep(0)


async def test_open_device_creates_one_tab(window):
    advertise(window, "AA:BB", "Thingy")
    window.open_device("AA:BB")
    await settle()
    assert window.tabs.count() == 3
    view = window.tabs.widget(2)
    assert isinstance(view, DeviceView)
    assert view.session.is_connected
    assert window.tabs.tabText(2) == "● Thingy"
    # the scanner entry's BLEDevice (not the bare address) is handed to bleak
    assert FakeClient.instances[-1].device.address == "AA:BB"

    window.open_device("AA:BB")  # second request focuses the existing tab
    await settle()
    assert window.tabs.count() == 3 and len(FakeClient.instances) == 1


async def test_close_tab_disconnects(window):
    window.open_device("CC:DD")  # not in scanner list: falls back to the address
    await settle()
    client = FakeClient.instances[-1]
    assert client.is_connected
    window._close_tab(2)
    await settle()
    assert window.tabs.count() == 2
    assert not client.is_connected
    assert "CC:DD" not in window.views


async def test_scanner_and_traffic_tabs_not_closable(window):
    window._close_tab(0)
    window._close_tab(1)
    assert window.tabs.count() == 2
    assert window.tabs.tabText(1) == "Traffic"


async def test_errors_reach_status_bar(window):
    FakeClient.fail_connect = True
    window.open_device("EE:FF")
    await settle()
    assert "Connection failed" in window.statusBar().currentMessage()


async def test_shutdown_disconnects_all(window):
    window.open_device("A1")
    window.open_device("A2")
    await settle()
    await window.shutdown()
    assert not any(c.is_connected for c in FakeClient.instances)


async def test_session_and_scanner_activity_is_logged(window):
    advertise(window, "AA:BB", "Thingy")
    window.open_device("AA:BB")
    await settle()
    view = window.views["AA:BB"]
    await view.session.read(3)
    window.scanner.error.emit("Bluetooth adapter is off")
    messages = [(e.source, e.level, e.message) for e in window.log_bus.entries]
    assert ("Thingy · AA:BB", "info", "Read Device Name (2A00): (0x) 46-61-6B-65-20-48-52-4D") in messages
    assert ("Scanner", "error", "Bluetooth adapter is off") in messages
    assert "Bluetooth adapter is off" in window.statusBar().currentMessage()


async def test_unhandled_errors_reach_log(window):
    import sys
    from bluebench.__main__ import install_error_handlers

    loop = asyncio.get_running_loop()
    old_hook, old_handler = sys.excepthook, loop.get_exception_handler()
    try:
        install_error_handlers(loop, window.log_bus)

        async def boom():
            raise RuntimeError("task exploded")
        task = asyncio.ensure_future(boom())
        await settle()
        del task  # an unretrieved task exception is reported when the task is collected
        import gc; gc.collect()
        await settle()
        try:
            raise ValueError("slot exploded")
        except ValueError as exc:
            sys.excepthook(type(exc), exc, exc.__traceback__)
    finally:
        sys.excepthook = old_hook
        loop.set_exception_handler(old_handler)
    errors = [e.message for e in window.log_bus.entries if e.level == "error"]
    assert any("task exploded" in m for m in errors)
    assert any("slot exploded" in m for m in errors)


async def test_close_disconnects_before_quitting(window, qapp, monkeypatch):
    quits = []
    monkeypatch.setattr(qapp, "quit", lambda: quits.append(True))
    window.show()
    window.open_device("AA:01")
    await settle()
    client = FakeClient.instances[-1]
    window.close()  # like clicking X
    assert not window.isVisible() and not quits and client.is_connected  # deferred
    await settle()
    assert not client.is_connected
    assert quits == [True]


async def test_close_with_stuck_device_still_quits(window, qapp, monkeypatch):
    from bluebench.ui import main_window
    quits = []
    monkeypatch.setattr(qapp, "quit", lambda: quits.append(True))
    monkeypatch.setattr(main_window, "SHUTDOWN_TIMEOUT_S", 0.05)

    async def hang():
        await asyncio.sleep(10)
    monkeypatch.setattr(window, "shutdown", hang)
    window.show()
    window.close()
    await asyncio.sleep(0.2)
    assert quits == [True]
    assert any("Shutdown incomplete" in e.message for e in window.log_bus.entries)
