import asyncio
from types import SimpleNamespace

import pytest
import shiboken6

from bluebench.ble import connection, link, pairing
from bluebench.ble.connection import DeviceSession
from bluebench.ui.device_view import DeviceView
from bluebench.ui.pairing_prompt import PairingPrompt
from fakes import FakeClient


@pytest.fixture
def view(qapp, monkeypatch):
    FakeClient.instances.clear()
    FakeClient.unpaired.clear()
    FakeClient.fail_connect = FakeClient.fail_pair = False
    monkeypatch.setattr(connection, "BleakClient", FakeClient)
    monkeypatch.setattr(pairing, "BleakClient", FakeClient)
    # FakeClient stands in for bleak, so take the generic path even on Windows.
    monkeypatch.setattr(pairing, "_is_winrt", lambda: False)
    v = DeviceView(DeviceSession("AA:BB", "Fake HRM"))
    v.logs = []
    v.session.log.connect(lambda lvl, msg: v.logs.append((lvl, msg)))
    yield v
    session = v.session
    shiboken6.delete(v)
    shiboken6.delete(session)


async def settle():
    for _ in range(5):
        await asyncio.sleep(0)


# -- session + header (non-Windows path: bleak pair/unpair) -----------------
async def test_pair_button_bonds(view):
    s = view.session
    assert not view.pair_btn.isEnabled()  # disconnected, bond state unknown
    await s.connect()
    assert s.bonded is None and view.pair_btn.text() == "Pair" and view.pair_btn.isEnabled()
    assert await view._pair()
    assert FakeClient.instances[-1].paired
    assert s.bonded is True
    assert view.pair_btn.text() == "Unpair" and "Bonded" in view.bond_label.text()
    assert ("info", "Bonded (unknown)") in view.logs


async def test_pair_failure_is_logged(view):
    await view.session.connect()
    FakeClient.fail_pair = True
    assert not await view._pair()
    assert view.session.bonded is None and not view.session.pairing_busy
    assert any(lvl == "error" and "AUTHENTICATION_FAILURE" in m for lvl, m in view.logs)
    assert view.pair_btn.text() == "Pair" and view.pair_btn.isEnabled()


async def test_unpair_disconnects_quietly(view):
    s = view.session
    await s.connect()
    await view._pair()
    view.pair_btn.click()  # now "Unpair"
    await settle()
    assert s.bonded is False and not s.is_connected
    assert FakeClient.unpaired == ["AA:BB"]
    # The drop caused by unpairing is expected, not a warning.
    assert not any("unexpectedly" in m for _, m in view.logs)
    assert view.pair_btn.text() == "Pair" and not view.pair_btn.isEnabled()


async def test_unpair_while_disconnected(view):
    s = view.session
    assert await s.unpair()
    assert FakeClient.unpaired == ["AA:BB"] and s.bonded is False


async def test_security_error_suggests_pairing(view, monkeypatch):
    await view.session.connect()

    async def denied(ch):
        raise RuntimeError("Insufficient Authentication")
    monkeypatch.setattr(FakeClient.instances[-1], "read_gatt_char", denied)
    assert await view.session.read(3) is None
    assert any("try Pair" in m for _, m in view.logs)


# -- WinRT pairing-request bridge -----------------------------------------
class FakeArgs:
    def __init__(self, kind, pin=""):
        self.pairing_kind, self.pin = kind, pin
        self.accepted = []
        self.completed = False

    def get_deferral(self):
        return SimpleNamespace(complete=lambda: setattr(self, "completed", True))

    def accept(self, *pin):
        self.accepted.append(pin)


@pytest.mark.parametrize("kind,pin,reply,expect", [
    (0x1, "", True, [()]),                       # Just Works
    (0x8, "123456", True, [()]),                 # numeric comparison: yes
    (0x8, "123456", False, []),                  # numeric comparison: no
    (0x4, "", "654321", [("654321",)]),          # passkey typed by the user
    (0x4, "", None, []),                         # passkey dialog cancelled
    (0x2, "111222", True, [()]),                 # we display, they type
])
async def test_handle_request(kind, pin, reply, expect):
    asked = []

    async def prompt(k, p):
        asked.append((k, p))
        return reply
    args = FakeArgs(kind, pin)
    pairing.handle_request(asyncio.get_running_loop(), prompt, args)
    await settle()
    assert asked == [(pairing.kind_name(kind), pin)]
    assert args.accepted == expect and args.completed


async def test_handle_request_prompt_error_still_completes():
    async def prompt(k, p):
        raise RuntimeError("boom")
    logs = []
    args = FakeArgs(0x8, "1")
    pairing.handle_request(asyncio.get_running_loop(), prompt, args, lambda *a: logs.append(a))
    await settle()
    assert args.completed and not args.accepted and logs[0][0] == "error"


def test_status_text():
    assert pairing.status_text("AUTHENTICATION_FAILURE").startswith("authentication failed")
    assert pairing.status_text("SOMETHING_NEW") == "something new"
    assert pairing.address_to_int("00:00:00:00:01:0A") == 0x10A


# -- prompt dialogs ------------------------------------------------------
async def test_prompt_numeric_comparison(qapp):
    from PySide6.QtWidgets import QMessageBox, QWidget
    parent = QWidget()
    prompt = PairingPrompt(parent, "Dev")
    task = asyncio.ensure_future(prompt("confirm_pin", "123456"))
    await settle()
    box = prompt.dialogs[0]
    assert "123456" in box.text()
    box.button(QMessageBox.StandardButton.Yes).click()
    assert await task is True
    prompt.close()
    shiboken6.delete(parent)


async def test_prompt_passkey_entry_and_close(qapp):
    from PySide6.QtWidgets import QWidget
    parent = QWidget()
    prompt = PairingPrompt(parent, "Dev")
    task = asyncio.ensure_future(prompt("provide_pin", ""))
    await settle()
    dlg = prompt.dialogs[0]
    dlg.setTextValue(" 012345 ")
    dlg.accept()
    assert await task == "012345"
    # Closing an unanswered dialog (pairing timed out) resolves as cancel.
    task = asyncio.ensure_future(prompt("provide_pin", ""))
    await settle()
    prompt.close()
    assert await task is None
    assert await prompt("confirm", "") is True
    assert await prompt("display_pin", "999") is True and "999" in prompt.dialogs[0].text()
    prompt.close()
    shiboken6.delete(parent)


# -- link info / connection priority ---------------------------------------
class FakePhy:
    def __init__(self, two_m):
        self.is_uncoded2_m_phy, self.is_uncoded1_m_phy, self.is_coded_phy = two_m, not two_m, False


class FakeDevice:
    def __init__(self):
        self.handlers = []
        self.interval = 24

    def get_connection_parameters(self):
        return SimpleNamespace(connection_interval=self.interval, connection_latency=0, link_timeout=400)

    def get_connection_phy(self):
        return SimpleNamespace(transmit_info=FakePhy(True), receive_info=FakePhy(True))

    def add_connection_parameters_changed(self, fn):
        self.handlers.append(fn)
        return len(self.handlers)

    def remove_connection_parameters_changed(self, token):
        self.handlers[token - 1] = None

    def request_preferred_connection_parameters(self, preset):
        return SimpleNamespace(status="ok", close=lambda: None)


def test_link_info_text():
    info = link.LinkInfo(30.0, 0, 4000, "2M", "2M")
    assert info.summary() == "30 ms · 2M PHY"
    assert "Supervision timeout: 4000 ms" in info.details()
    assert link.LinkInfo(7.5, tx_phy="2M", rx_phy="1M").summary() == "7.5 ms · 2M/1M PHY"
    assert link.LinkInfo().summary() == ""


async def test_link_shown_and_updated(view, monkeypatch):
    dev = FakeDevice()
    monkeypatch.setattr(link, "_device", lambda client: dev if client is not None else None)
    s = view.session
    await s.connect()
    assert s.link == link.LinkInfo(30.0, 0, 4000, "2M", "2M")
    assert view.link_label.text() == "MTU 247 · 30 ms · 2M PHY"
    dev.interval = 6                       # peripheral asked for 7.5 ms
    for fn in dev.handlers:
        if fn:
            fn(dev, None)                  # WinRT fires the event on its own thread
    await settle()
    assert "7.5 ms" in view.link_label.text()
    assert any("Link updated" in m for _, m in view.logs)
    await s.disconnect()
    assert s.link is None and view.link_label.text() == ""
    assert dev.handlers == [None]          # watcher removed on disconnect


async def test_priority_menu(view, monkeypatch):
    s = view.session
    assert not view.priority_menu.isEnabled()
    await s.connect()
    assert not view.priority_menu.isEnabled()  # no WinRT device on this platform
    req = SimpleNamespace(closed=False)
    req.close = lambda: setattr(req, "closed", True)
    monkeypatch.setattr(link, "supports_priority", lambda client: True)
    monkeypatch.setattr(link, "request_priority", lambda client, p: req)
    view._update_link_header()
    assert view.priority_menu.isEnabled()
    view.priority_actions["throughput"].trigger()
    assert s.priority == "throughput" and view.priority_actions["throughput"].isChecked()
    assert "High throughput" in view.link_label.toolTip()
    await s.disconnect()
    assert req.closed and s.priority is None


async def test_priority_failure_logged(view, monkeypatch):
    await view.session.connect()

    def fail(client, p):
        raise RuntimeError("not supported on this system (needs Windows 11)")
    monkeypatch.setattr(link, "request_priority", fail)
    assert not view.session.request_priority("power")
    assert any(lvl == "error" and "Windows 11" in m for lvl, m in view.logs)
