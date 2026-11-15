import asyncio

import pytest
import shiboken6

from bluebench.ble import connection
from bluebench.ble.connection import DeviceSession
from bluebench.ble.formats import ValueFormat
from bluebench.ui import device_view
from bluebench.ui.device_view import COL_NAME, COL_PROPS, COL_VALUE, DeviceView
from fakes import FakeClient


@pytest.fixture
def view(qapp, monkeypatch):
    FakeClient.instances.clear()
    FakeClient.fail_connect = False
    monkeypatch.setattr(connection, "BleakClient", FakeClient)
    v = DeviceView(DeviceSession("AA:BB", "Fake HRM"))
    yield v
    session = v.session
    shiboken6.delete(v)
    shiboken6.delete(session)


def find(view, name):
    return next(it for it in view._items.values() if it.text(COL_NAME) == name)


async def settle():
    for _ in range(5):
        await asyncio.sleep(0)


async def test_tree_populates_with_names(view):
    titles = []
    view.title_changed.connect(titles.append)
    await view.session.connect()
    top = [view.tree.topLevelItem(i).text(COL_NAME) for i in range(view.tree.topLevelItemCount())]
    assert top == ["Generic Access", "Heart Rate", "Nordic UART Service"]
    assert find(view, "NUS RX").text(COL_PROPS) == "W WNR"
    assert titles[-1] == "● Fake HRM"
    assert view.link_label.text() == "MTU 247"


async def test_read_and_format(view):
    await view.session.connect()
    view.tree.setCurrentItem(find(view, "Device Name"))
    assert view.read_btn.isEnabled() and view.write_btn.isEnabled()
    assert not view.notify_btn.isEnabled()
    view._read_selected()
    await settle()
    assert find(view, "Device Name").text(COL_VALUE) == "Fake HRM"  # UTF-8 by default
    view.format_combo.setCurrentIndex(list(ValueFormat).index(ValueFormat.HEX))
    view._format_changed(0)
    assert find(view, "Device Name").text(COL_VALUE).startswith("0x46-61")


async def test_subscribe_and_count(view):
    await view.session.connect()
    hrm = find(view, "Heart Rate Measurement")
    view.tree.setCurrentItem(hrm)
    assert view.notify_btn.isEnabled() and not view.read_btn.isEnabled()
    view._toggle_notify_selected()
    await settle()
    assert view.notify_btn.text() == "Unsubscribe"
    assert hrm.text(COL_PROPS).endswith("⟳")
    client = FakeClient.instances[-1]
    client.push_notification(12, b"\x00\x48")
    client.push_notification(12, b"\x00\x49")
    assert hrm.text(COL_VALUE) == "0x00-49    [2 notifications]"


async def test_cccd_not_writable_via_dialog(view):
    await view.session.connect()
    cccd = next(it for it in view._items.values()
                if it.data(COL_NAME, device_view.KIND_ROLE) == "cccd")
    view.tree.setCurrentItem(cccd)
    assert view.read_btn.isEnabled() and not view.write_btn.isEnabled()


async def test_disconnect_greys_out(view):
    await view.session.connect()
    FakeClient.instances[-1].drop_link()
    assert not view.tree.isEnabled()
    assert view.connect_btn.text() == "Connect"


async def test_read_all(view):
    await view.session.connect()
    await view._read_all()
    assert find(view, "Body Sensor Location").text(COL_VALUE) == "1"
