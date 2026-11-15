"""Traffic tab UI: filtering, details/hex linkage, export."""
from __future__ import annotations

import csv

import pytest

from bluebench.sniffer import nrf_sniffer as ns
from bluebench.sniffer.app_capture import AppCapture
from bluebench.sniffer.ll_decode import LLDecoder
from bluebench.sniffer.model import CapturedPacket, PacketStore
from bluebench.ui import traffic_view as tv
from fake_air import C2P, CENTRAL, P2C, PERIPH, adv_ind, connect_ind, data_pdu, l2cap, meta


@pytest.fixture
def view(qapp, monkeypatch):
    monkeypatch.setattr(tv, "list_ports", lambda: [ns.PortInfo("COM7", "nRF Sniffer", True)])
    store = PacketStore()
    v = tv.TrafficView(store, AppCapture(store), ns.AirCapture(store))
    d = LLDecoder()
    store.add_many([
        d.decode(adv_ind(PERIPH, b"\x07\x09Thingy"), meta(), 0.1),
        d.decode(connect_ind(CENTRAL, PERIPH), meta(), 0.2),
        d.decode(data_pdu(1, b""), meta(C2P), 0.3),
        d.decode(data_pdu(2, l2cap(4, b"\x12\x10\x00\x01\x00")), meta(C2P), 0.4),
        d.decode(data_pdu(2, l2cap(4, b"\x01\x12\x10\x00\x03")), meta(P2C), 0.5),
    ])
    store.add(CapturedPacket("app", 0.6, "BlueBench", PERIPH, "GATT", "Write Request, 0x0010",
                             raw=b"\x01\x00"))
    yield v
    v.deleteLater()


def infos(v) -> list[str]:
    return [p.info for p in v.model.rows]


def test_empty_pdus_hidden_by_default(view):
    assert len(view.model.rows) == 5 and not any(p.empty for p in view.model.rows)
    view.hide_empty_check.setChecked(False)
    assert len(view.model.rows) == 6
    assert view.count_label.text() == "6 packets"


def test_filter_tokens_and_negation(view):
    view.model.set_filter("att sent write")
    assert infos(view) == ["Sent Write Request, Handle: 0x0010, Value: 01 00"]
    view.model.set_filter("write")      # ATT write, ATT error about a write, app write
    assert len(view.model.rows) == 3
    view.model.set_filter("write !air")
    assert [p.iface for p in view.model.rows] == ["app"]
    view.model.set_filter("error")
    assert len(view.model.rows) == 1 and view.model.rows[0].error
    assert view.count_label.text() == "1 / 6 packets"


def test_new_packets_respect_filter(view):
    view.model.set_filter("gatt")
    view.store.add(CapturedPacket("app", 1.0, "a", "b", "GATT", "Notification"))
    view.store.add(CapturedPacket("air", 1.1, "a", "b", "LE LL", "ADV_IND"))
    assert infos(view)[-1] == "Notification" and len(view.model.rows) == 2


def test_details_and_hex_highlight(view):
    view.table.selectRow(2)          # the ATT Write Request (empty PDU #3 is hidden)
    p = view._current
    assert p.protocol == "ATT"
    tops = [view.details.topLevelItem(i).text(0) for i in range(view.details.topLevelItemCount())]
    assert tops[0].startswith("Packet 4:") and any(t.startswith("Attribute Protocol") for t in tops)
    assert view.hex_view.toPlainText().startswith("0000  1B 4A 65 50")
    view.highlight((0, 4))           # access address
    sels = view.hex_view.extraSelections()
    assert [s.cursor.selectedText() for s in sels[:2]] == ["1B", "."]
    assert len(sels) == 8


def test_hex_positions_match_dump():
    data = bytes(range(40))
    text = tv.hex_dump(data)
    for i in (0, 15, 16, 39):
        hx, asc = tv.hex_positions(i)
        assert text[hx:hx + 2] == f"{i:02X}"
        assert text[asc] == (chr(i) if 32 <= i < 127 else ".")


def test_export_csv(view, tmp_path):
    path = tmp_path / "t.csv"
    assert view.export_csv(str(path)) == 5
    rows = list(csv.reader(path.open(encoding="utf-8")))
    assert rows[0][:7] == tv.COLUMNS and rows[-1][7] == "app" and rows[-1][8] == "01 00"


def test_sniffer_controls(view):
    assert view.port_combo.currentData() == "COM7"
    assert view.sniff_btn.isEnabled() and not view.follow_combo.isEnabled()
    view.air.advertisers[PERIPH] = ns.Advertiser(PERIPH, True, "Thingy", -50, 3, True)
    view._refresh_follow()
    assert view.follow_combo.count() == 2 and "Thingy" in view.follow_combo.itemText(1)


def test_app_toggle(view):
    view.app_check.setChecked(False)
    assert not view.app_capture.enabled
