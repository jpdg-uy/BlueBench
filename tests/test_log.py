import csv

import pytest

from bluebench.core.log_bus import LogBus, export, level_rank
from bluebench.ui.log_view import LogView


@pytest.fixture
def bus(qapp):
    return LogBus(max_entries=5)


def test_bus_ring_and_sources(bus):
    for i in range(7):
        bus.log("info", "Dev A" if i % 2 else "Dev B", f"msg {i}")
    assert [e.message for e in bus.entries] == [f"msg {i}" for i in range(2, 7)]
    assert bus.sources() == ["Dev A", "Dev B"]
    assert bus.log("bogus", "X", "y").level == "info"
    assert level_rank("debug") < level_rank("error")


def test_export_txt_and_csv(bus, tmp_path):
    bus.log("info", "Dev", "Read Battery Level (2A19): (0x) 64")
    bus.log("error", "Dev", 'Write failed: "denied", code 3')
    txt = tmp_path / "log.txt"
    export(list(bus.entries), txt)
    lines = txt.read_text("utf-8").splitlines()
    assert len(lines) == 2 and "ERROR" in lines[1] and lines[1].endswith('Dev: Write failed: "denied", code 3')
    out = tmp_path / "log.csv"
    export(list(bus.entries), out)
    rows = list(csv.reader(out.open(encoding="utf-8")))
    assert rows[0] == ["timestamp", "level", "source", "message"]
    assert rows[2][1:] == ["error", "Dev", 'Write failed: "denied", code 3']


def test_view_batches_filters_and_trims(bus):
    view = LogView(bus)
    bus.log("debug", "A", "noise")
    bus.log("info", "A", "hello")
    bus.log("error", "B", "boom")
    assert view.model.rowCount() == 0  # nothing until the batch flush
    view.flush()
    assert view.model.rowCount() == 3
    assert [e.message for e in view.visible_entries()] == ["hello", "boom"]  # debug hidden
    view.level_combo.setCurrentIndex(0)
    assert view.proxy.rowCount() == 3
    view.source_combo.setCurrentIndex(view.source_combo.findText("B"))
    assert [e.message for e in view.visible_entries()] == ["boom"]
    view.source_combo.setCurrentIndex(0)
    view.search.setText("HEL")
    assert [e.message for e in view.visible_entries()] == ["hello"]
    view.search.setText("")
    for i in range(4):
        bus.log("info", "C", f"x{i}")
    view.flush()
    assert view.model.rowCount() == 5  # trimmed to the bus limit
    bus.clear()
    assert view.model.rowCount() == 0 and view.source_combo.count() == 1


def test_copy_selected(bus, qapp):
    view = LogView(bus)
    bus.log("info", "A", "first")
    bus.log("info", "A", "second")
    view.flush()
    view.table.selectAll()
    view.copy_selected()
    text = qapp.clipboard().text()
    assert "A: first" in text and "A: second" in text
