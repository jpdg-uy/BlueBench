import asyncio
import time

import pytest
import shiboken6
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from PySide6.QtCore import QCoreApplication, QEvent, Qt

from bluebench.ble import pairing
from bluebench.ble.scanner import DeviceEntry, Scanner
from bluebench.ui.scanner_view import COL_BOND, COL_NAME, COL_RSSI, ScannerView

DEVICES = [("AA:00:00:00:00:01", "Alpha", -80), ("AA:00:00:00:00:02", "Bravo", -50),
           ("AA:00:00:00:00:03", "Charlie", -65)]


@pytest.fixture
def view(qapp, monkeypatch):
    async def unknown():
        return None
    monkeypatch.setattr(pairing, "bonded_addresses", unknown)
    scanner = Scanner()
    v = ScannerView(scanner)
    for addr, name, rssi in DEVICES:
        scanner._on_detection(BLEDevice(addr, name, None),
                              AdvertisementData(name, {}, {}, [], None, rssi, ()))
    v.model.flush()
    yield v
    shiboken6.delete(v)
    shiboken6.delete(scanner)


def order(v):
    return [v.proxy.index(r, COL_NAME).data(Qt.ItemDataRole.UserRole) for r in range(v.proxy.rowCount())]


async def settle():
    for _ in range(5):
        await asyncio.sleep(0)


def test_pinned_rows_float_in_discovery_order(view):
    a, b, c = (d[0] for d in DEVICES)
    assert order(view) == [a, b, c]
    view.set_pinned(c, True)
    assert order(view) == [c, a, b]
    assert view.proxy.index(0, COL_NAME).data() == "📌 Charlie"
    assert view.proxy.index(0, COL_NAME).data(Qt.ItemDataRole.FontRole).bold()
    view.set_pinned(c, False)
    assert order(view) == [a, b, c]


@pytest.mark.parametrize("direction", [Qt.SortOrder.AscendingOrder, Qt.SortOrder.DescendingOrder])
def test_pinned_rows_stay_on_top_when_sorted(view, direction):
    a, b, c = (d[0] for d in DEVICES)
    view.set_pinned(a, True)                      # the weakest one
    view.table.sortByColumn(COL_RSSI, direction)
    rest = [c, b] if direction == Qt.SortOrder.AscendingOrder else [b, c]
    assert order(view) == [a] + rest
    view.table.sortByColumn(COL_NAME, Qt.SortOrder.DescendingOrder)
    assert order(view) == [a, c, b]              # name sort ignores the pin marker


def test_pins_survive_clear(view):
    view.set_pinned(DEVICES[2][0], True)
    view._clear()
    view.scanner._on_detection(BLEDevice(DEVICES[2][0], "Charlie", None),
                               AdvertisementData("Charlie", {}, {}, [], None, -60, ()))
    view.model.flush()
    assert view.proxy.index(0, COL_NAME).data() == "📌 Charlie"


async def test_bond_column_hidden_when_unknown(view):
    await view.refresh_bonds()
    assert view.table.isColumnHidden(COL_BOND)


async def test_bond_column_marks_bonded(view, monkeypatch):
    async def bonded():
        return {"AA:00:00:00:00:02"}
    monkeypatch.setattr(pairing, "bonded_addresses", bonded)
    await view.refresh_bonds()
    assert not view.table.isColumnHidden(COL_BOND)
    marks = [view.proxy.index(r, COL_BOND).data() for r in range(3)]
    assert marks == ["", "🔒", ""]
    assert "Bonded" in view.proxy.index(1, COL_BOND).data(Qt.ItemDataRole.ToolTipRole)
    view.table.sortByColumn(COL_BOND, Qt.SortOrder.AscendingOrder)
    assert order(view)[0] == "AA:00:00:00:00:02"


async def test_request_refresh_does_not_stack(view, monkeypatch):
    calls = []

    async def slow():
        calls.append(1)
        await asyncio.sleep(0.05)
        return set()
    monkeypatch.setattr(pairing, "bonded_addresses", slow)
    view.request_bond_refresh()
    view.request_bond_refresh()
    await asyncio.sleep(0.1)
    assert calls == [1]


def test_address_from_device_id():
    assert pairing.address_from_device_id(
        "BluetoothLE#BluetoothLE00:1a:7d:da:71:13-c4:3a:7e:12:9b:01") == "C4:3A:7E:12:9B:01"
    assert pairing.address_from_device_id("garbage") is None


@pytest.mark.skipif(__import__("sys").platform == "win32", reason="real OS query on Windows")
async def test_bonded_addresses_unknown_off_windows():
    assert await pairing.bonded_addresses() is None


@pytest.mark.parametrize("text, ok", [("5900", True), ("0x5900", True), ("59 00 aa", True),
                                      ("59:00", True), ("0059aa", True), ("5900ab", False),
                                      ("0059", True), ("590", False)])
def test_filter_by_manufacturer_data_hex(view, text, ok):
    # On air: 59 00 AA BB -> company ID 0x0059, payload AA BB.
    sensor = "AA:00:00:00:00:09"
    view.scanner._on_detection(BLEDevice(sensor, None, None),
                               AdvertisementData(None, {0x0059: b"\xaa\xbb"}, {}, [], None, -60, ()))
    view.model.flush()
    view.proxy.set_filter(text=text)
    assert (sensor in order(view)) is ok
    assert "AA:00:00:00:00:01" not in order(view)  # no manufacturer data



# -- stable order: averaged RSSI, periodic re-sort, pause on hover, filter hysteresis --

A, B, C = (d[0] for d in DEVICES)        # Alpha -80, Bravo -50, Charlie -65


def set_rssi(view, address, *values, age=0.0):
    """Replace a device's RSSI history with ``values`` received now (or ``age`` s ago)."""
    e = view.scanner.devices[address]
    t = time.monotonic() - age
    e.rssi_history.clear()
    e.rssi_history.extend((t, v) for v in values)
    e.rssi = values[-1]


def add_device(view, address, name, rssi):
    view.scanner._on_detection(BLEDevice(address, name, None),
                               AdvertisementData(name, {}, {}, [], None, rssi, ()))
    view.model.flush()


def test_smoothed_rssi_averages_recent_packets():
    e = DeviceEntry("AA:00:00:00:00:01", None, -60, None, None)
    now = 100.0
    e.rssi_history.extend([(now - 10, -90), (now - 2, -70), (now - 1, -60), (now, -62)])
    assert e.smoothed_rssi(now=now) == -64            # the -90 is outside the window
    assert e.smoothed_rssi(window_s=0.5, now=now) == -62
    e.rssi_history.clear()
    assert e.smoothed_rssi(now=now) == -60            # nothing recent: the latest value


def test_rssi_sort_waits_for_the_next_snapshot(view):
    view.table.sortByColumn(COL_RSSI, Qt.SortOrder.DescendingOrder)
    assert order(view) == [B, C, A]
    set_rssi(view, A, -40)                 # Alpha is now the strongest...
    view.model.flush()
    assert order(view) == [B, C, A]        # ...but rows only move on the periodic re-sort
    assert view.proxy.index(2, COL_RSSI).data() == "-40 dBm"   # the value itself is live
    view.model.refresh_sort_keys()
    view.model.flush()
    assert order(view) == [A, B, C]


def test_rssi_sort_uses_the_average_not_one_packet(view):
    view.table.sortByColumn(COL_RSSI, Qt.SortOrder.DescendingOrder)
    set_rssi(view, A, -66, -66, -66, -30)  # one strong packet: average -57, below Bravo
    view.model.refresh_sort_keys()
    view.model.flush()
    assert order(view) == [B, A, C]


def test_equal_rssi_keeps_discovery_order(view):
    for addr in (A, B, C):
        set_rssi(view, addr, -70)
    view.model.refresh_sort_keys()
    for direction in (Qt.SortOrder.DescendingOrder, Qt.SortOrder.AscendingOrder):
        view.table.sortByColumn(COL_RSSI, direction)
        first = order(view)
        for _ in range(3):
            view.model.flush()
            assert order(view) == first


def test_order_pauses_while_the_pointer_is_over_the_list(view):
    view.table.sortByColumn(COL_RSSI, Qt.SortOrder.DescendingOrder)
    QCoreApplication.sendEvent(view.table.viewport(), QEvent(QEvent.Type.Enter))
    assert view.order_paused
    view._tick()
    assert "order paused" in view.count_label.text()

    set_rssi(view, A, -40)
    view.model.refresh_sort_keys()
    view.model.flush()
    add_device(view, "AA:00:00:00:00:04", "Delta", -30)
    assert order(view) == [B, C, A, "AA:00:00:00:00:04"]     # nothing moved, new one at the end

    QCoreApplication.sendEvent(view.table.viewport(), QEvent(QEvent.Type.Leave))
    assert not view.order_paused
    assert order(view) == ["AA:00:00:00:00:04", A, B, C]     # caught up at once
    view._tick()
    assert "paused" not in view.count_label.text()


def test_selection_follows_the_device_when_rows_move(view):
    view.table.sortByColumn(COL_RSSI, Qt.SortOrder.DescendingOrder)
    view.table.selectRow(2)
    assert view.selected_address() == A
    set_rssi(view, A, -40)
    view.model.refresh_sort_keys()
    view.model.flush()
    assert order(view)[0] == A and view.selected_address() == A


def test_min_rssi_filter_has_hysteresis(view):
    view.rssi_spin.setValue(-70)
    assert order(view) == [B, C]                       # Alpha (-80) is hidden
    set_rssi(view, C, -72)                             # just below: Charlie stays
    view.model.flush()
    assert C in order(view)
    set_rssi(view, C, -74)                             # past the margin: hidden
    view.model.flush()
    assert C not in order(view)
    set_rssi(view, C, -71)                             # coming back needs the real threshold
    view.model.flush()
    assert C not in order(view)
    set_rssi(view, C, -69)
    view.model.flush()
    assert C in order(view)


def test_min_rssi_filter_ignores_a_single_weak_packet(view):
    view.rssi_spin.setValue(-70)
    set_rssi(view, C, -65, -65, -65, -95)              # one weak packet: average -72...
    view.model.flush()
    assert C in order(view)                            # ...within the margin, still shown


def test_row_follows_a_rotating_private_address(view, monkeypatch):
    from types import SimpleNamespace
    from bluebench.ble import scanner as scanner_mod
    from bluebench.ui.scanner_view import COL_ADDRESS
    rnd = SimpleNamespace(adv=SimpleNamespace(bluetooth_address_type=SimpleNamespace(name="RANDOM")),
                          scan=None)
    clock = iter([1000.0, 1005.0])
    for addr in ("4A:00:00:00:00:01", "5B:00:00:00:00:02"):
        t = next(clock)
        monkeypatch.setattr(scanner_mod.time, "monotonic", lambda t=t: t)
        view.scanner._on_detection(BLEDevice(addr, "Rotor", rnd),
                                   AdvertisementData("Rotor", {}, {}, [], None, -60, ()))
        view.model.flush()
        if addr.startswith("4A"):
            view.set_pinned(addr, True)
    assert view.model.rowCount() == len(DEVICES) + 1
    top = view.proxy.index(0, COL_ADDRESS)
    assert top.data(Qt.ItemDataRole.UserRole) == "4A:00:00:00:00:01"   # row key stays
    assert top.data() == "5B:00:00:00:00:02  RPA  ↻2"                    # shows the new one
    assert "2 addresses" in top.data(Qt.ItemDataRole.ToolTipRole)
    view.proxy.set_filter(text="4a:00")                                  # old address still found
    assert view.proxy.rowCount() == 1
