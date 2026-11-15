import asyncio

import pytest

from bluebench.ble import connection
from bluebench.ble.connection import DeviceSession
from bluebench.tools.nus import NusClient, chunks, has_nus
from bluebench.ui.device_view import DeviceView
from fakes import NUS_RX_HANDLE, NUS_TX_HANDLE, FakeClient, default_gatt


@pytest.fixture
def fake(qapp, monkeypatch):
    FakeClient.instances.clear()
    FakeClient.fail_connect = False
    FakeClient.gatt_factory = staticmethod(default_gatt)
    monkeypatch.setattr(connection, "BleakClient", FakeClient)
    yield
    FakeClient.gatt_factory = staticmethod(default_gatt)


async def settle():
    for _ in range(10):
        await asyncio.sleep(0)


def test_chunks():
    assert chunks(b"abcdefg", 3) == [b"abc", b"def", b"g"]
    assert chunks(b"", 20) == [b""]


async def test_start_send_receive_echo(fake):
    s = DeviceSession("AA", "uart")
    await s.connect()
    assert has_nus(s)
    nus = NusClient(s)
    got = []
    nus.received.connect(got.append)
    assert await nus.start()
    assert nus.active and NUS_TX_HANDLE in s.notifying
    assert await nus.send(b"hello\n")
    assert got == [b"hello\n"]
    assert (nus.tx_bytes, nus.rx_bytes) == (6, 6)
    # write without response is preferred when RX supports it
    assert FakeClient.instances[-1].writes[-1] == (NUS_RX_HANDLE, b"hello\n", False)


async def test_send_is_chunked_to_max_write_size(fake):
    s = DeviceSession("AA", "uart")
    await s.connect()
    s.find_characteristic("6e400002-b5a3-f393-e0a9-e50e24dcca9e").max_write_without_response_size = 20
    nus = NusClient(s)
    assert nus.chunk_size() == 20
    assert await nus.send(bytes(range(50)))
    sizes = [len(d) for h, d, _ in FakeClient.instances[-1].writes if h == NUS_RX_HANDLE]
    assert sizes == [20, 20, 10]


async def test_unsubscribe_from_tree_and_disconnect_deactivate(fake):
    s = DeviceSession("AA", "uart")
    await s.connect()
    nus = NusClient(s)
    await nus.start()
    await s.set_notify(NUS_TX_HANDLE, False)  # e.g. via the GATT tree
    assert not nus.active
    await nus.start()
    FakeClient.instances[-1].drop_link()
    assert not nus.active


async def test_no_nus_reports_error(fake):
    FakeClient.gatt_factory = staticmethod(lambda: default_gatt()[:2])
    s = DeviceSession("AA", "hrm")
    await s.connect()
    assert not has_nus(s)
    nus = NusClient(s)
    errors = []
    nus.error.connect(errors.append)
    assert not await nus.start()
    assert errors


async def test_uart_tab_only_with_nus(fake):
    FakeClient.gatt_factory = staticmethod(lambda: default_gatt()[:2])
    v = DeviceView(DeviceSession("AA", "hrm"))
    await v.session.connect()
    assert v.pages.count() == 2 and v.uart_view is None  # GATT + Macros


async def test_uart_view_flow(fake):
    v = DeviceView(DeviceSession("AA", "uart"))
    await v.session.connect()
    assert v.pages.tabText(1) == "UART"
    v.pages.setCurrentIndex(1)  # first open auto-subscribes
    await settle()
    assert v.nus.active and v.uart_view.enable_btn.text() == "Disable"

    u = v.uart_view
    u.input.setText("ping")
    u._send()
    await settle()
    out = u.text()
    assert "→ ping" in out and "ping" in out.split("→ ping", 1)[1]
    assert u.input.text() == "" and u.input.history == ["ping"]

    # streaming text: partial lines join, newlines break
    u.clear()
    u.echo.setChecked(False)
    for part in (b"tem", b"p=21", b"\r\nhum=40\n"):
        FakeClient.instances[-1].push_notification(NUS_TX_HANDLE, part)
    assert u.text().splitlines() == ["temp=21", "hum=40"]

    # hex input and display
    u.clear()
    u.send_format.setCurrentText("Hex")
    u.display_combo.setCurrentText("Hex")
    u.input.setText("01 02 ff")
    assert u.build_payload() == b"\x01\x02\xff"
    u.input.setText("zz")
    u._send()
    assert "! Hex input contains non-hex characters" in u.text()


async def test_uart_resumes_after_reconnect(fake):
    v = DeviceView(DeviceSession("AA", "uart"))
    await v.session.connect()
    v.pages.setCurrentIndex(1)
    await settle()
    assert v.nus.active
    FakeClient.instances[-1].drop_link()
    assert not v.nus.active and not v.uart_view.send_btn.isEnabled()
    await v.session.connect()
    await settle()
    assert v.nus.active and v.pages.count() == 3  # no duplicate UART tab
    assert v.uart_view.send_btn.isEnabled()
