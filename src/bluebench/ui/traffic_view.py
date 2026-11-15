"""Traffic tab: a Wireshark-style packet list fed by two sources.

* App: what BlueBench itself does (GATT operations, notifications, errors).
* Air: everything on the radio, from an nRF Sniffer for Bluetooth LE dongle/DK.

Layout: packet list on top; packet details tree and hex dump below.
Selecting a details node highlights its bytes in the hex dump.
"""
from __future__ import annotations

import csv

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QFont, QFontDatabase, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
                               QPushButton, QSplitter, QTableView, QTextEdit, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from ..sniffer import pcap
from ..sniffer.app_capture import AppCapture
from ..sniffer.model import CapturedPacket, Field, PacketStore
from ..sniffer.nrf_sniffer import AirCapture, list_ports

COLUMNS = ["No.", "Time", "Source", "Destination", "Protocol", "Length", "Info"]
COL_NO, COL_TIME, COL_SRC, COL_DST, COL_PROTO, COL_LEN, COL_INFO = range(len(COLUMNS))
PROTO_COLORS = {
    "ATT": QColor(66, 133, 244, 45),
    "GATT": QColor(52, 168, 83, 45),
    "SMP": QColor(156, 39, 176, 45),
    "L2CAP": QColor(0, 150, 136, 40),
    "LINK": QColor(255, 152, 0, 55),
}
LL_CONTROL_COLOR = QColor(255, 193, 7, 50)
ERROR_COLOR = QColor(229, 57, 53, 70)
SPAN_ROLE = Qt.ItemDataRole.UserRole + 1
SCAN_ALL = "__scan__"
ADV_REFRESH_MS = 1000


# -- filtering -----------------------------------------------------------------
def compile_filter(text: str):
    """Space-separated tokens, all must match (substring, case-insensitive).
    ``!token`` excludes. Searches interface, protocol, addresses and info."""
    pos, neg = [], []
    for tok in text.lower().split():
        if tok.startswith("!") and len(tok) > 1:
            neg.append(tok[1:])
        elif tok != "!":
            pos.append(tok)

    def accept(p: CapturedPacket) -> bool:
        h = p.haystack()
        return all(t in h for t in pos) and not any(t in h for t in neg)
    return accept


class PacketModel(QAbstractTableModel):
    """The packets of a PacketStore that pass the current filter."""

    def __init__(self, store: PacketStore, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.rows: list[CapturedPacket] = []
        self._accept = compile_filter("")
        self.hide_empty = True
        self.show_app = True
        self.show_air = True
        store.appended.connect(self._on_appended)
        store.reset.connect(self.rebuild)
        self.rebuild()

    def _passes(self, p: CapturedPacket) -> bool:
        if p.empty and self.hide_empty:
            return False
        if (p.iface == "app" and not self.show_app) or (p.iface == "air" and not self.show_air):
            return False
        return self._accept(p)

    def set_filter(self, text: str) -> None:
        self._accept = compile_filter(text)
        self.rebuild()

    def rebuild(self) -> None:
        self.beginResetModel()
        self.rows = [p for p in self.store.packets if self._passes(p)]
        self.endResetModel()

    def _on_appended(self, start: int, count: int) -> None:
        new = [p for p in self.store.packets[start:start + count] if self._passes(p)]
        if not new:
            return
        first = len(self.rows)
        self.beginInsertRows(QModelIndex(), first, first + len(new) - 1)
        self.rows.extend(new)
        self.endInsertRows()

    def packet(self, row: int) -> CapturedPacket:
        return self.rows[row]

    # Qt model API
    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        p = self.rows[index.row()]
        col = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            return (str(p.number), f"{p.time:.6f}", p.source, p.destination,
                    p.protocol, str(p.length), p.info)[col]
        if role == Qt.ItemDataRole.BackgroundRole:
            return QBrush(row_color(p)) if row_color(p) else None
        if role == Qt.ItemDataRole.ToolTipRole and col == COL_INFO:
            return p.info
        if role == Qt.ItemDataRole.TextAlignmentRole and col in (COL_NO, COL_TIME, COL_LEN):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None


def row_color(p: CapturedPacket) -> QColor | None:
    if p.error:
        return ERROR_COLOR
    if p.protocol == "LE LL" and p.info.startswith("LL_"):
        return LL_CONTROL_COLOR
    return PROTO_COLORS.get(p.protocol)


# -- hex dump ----------------------------------------------------------------
BYTES_PER_LINE = 16
HEX_COL = 6          # "0000  "
LINE_LEN = HEX_COL + BYTES_PER_LINE * 3 + 1 + BYTES_PER_LINE   # hex, gap, ascii


def hex_dump(data: bytes) -> str:
    lines = []
    for off in range(0, len(data), BYTES_PER_LINE):
        chunk = data[off:off + BYTES_PER_LINE]
        hexpart = " ".join(f"{b:02X}" for b in chunk).ljust(BYTES_PER_LINE * 3 - 1)
        text = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{off:04X}  {hexpart}  {text}")
    return "\n".join(lines)


def hex_positions(index: int) -> tuple[int, int]:
    """Character offsets (hex digits, ascii char) of byte ``index`` in hex_dump()."""
    line, col = divmod(index, BYTES_PER_LINE)
    base = line * (LINE_LEN + 1)   # +1 for the newline
    return base + HEX_COL + col * 3, base + HEX_COL + BYTES_PER_LINE * 3 + 1 + col


# -- view --------------------------------------------------------------------
class TrafficView(QWidget):
    def __init__(self, store: PacketStore, app_capture: AppCapture, air: AirCapture,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.store, self.app_capture, self.air = store, app_capture, air
        self.model = PacketModel(store, self)

        # Row 1: sources
        self.app_check = QCheckBox("App traffic")
        self.app_check.setChecked(True)
        self.app_check.setToolTip("Record the GATT operations BlueBench performs on its own connections")
        self.app_check.toggled.connect(self._on_app_toggled)
        self.port_combo = QComboBox()
        self.port_combo.setMinimumWidth(260)
        self.port_combo.setToolTip("Serial port of an nRF Sniffer for Bluetooth LE (★ = Nordic/SEGGER device)")
        self.refresh_ports_btn = QPushButton("↻")
        self.refresh_ports_btn.setToolTip("Refresh serial ports")
        self.refresh_ports_btn.setFixedWidth(32)
        self.refresh_ports_btn.clicked.connect(self.refresh_ports)
        self.sniff_btn = QPushButton("Start sniffer")
        self.sniff_btn.clicked.connect(self._toggle_sniffer)
        self.follow_combo = QComboBox()
        self.follow_combo.setMinimumWidth(300)
        self.follow_combo.setToolTip("Which device the sniffer follows into a connection")
        self.follow_combo.activated.connect(self._on_follow_chosen)
        self.air_state = QLabel("Sniffer: idle")

        src_row = QHBoxLayout()
        src_row.addWidget(self.app_check)
        src_row.addSpacing(16)
        on_air = QLabel("On-air (experimental):")
        on_air.setToolTip("Capture with an nRF Sniffer for Bluetooth LE dongle/DK. "
                          "Not yet validated against every firmware version; please report issues.")
        src_row.addWidget(on_air)
        src_row.addWidget(self.port_combo)
        src_row.addWidget(self.refresh_ports_btn)
        src_row.addWidget(self.sniff_btn)
        src_row.addWidget(QLabel("Follow:"))
        src_row.addWidget(self.follow_combo, 1)
        src_row.addWidget(self.air_state)

        # Row 2: display
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter: e.g.  att write   ·   smp   ·   !adv_ind   ·   air ll_   ·   C4:12")
        self.filter_edit.setClearButtonEnabled(True)
        self._filter_timer = QTimer(self, singleShot=True, interval=200)
        self._filter_timer.timeout.connect(lambda: self.model.set_filter(self.filter_edit.text()))
        self.filter_edit.textChanged.connect(lambda _: self._filter_timer.start())
        self.hide_empty_check = QCheckBox("Hide empty PDUs")
        self.hide_empty_check.setChecked(True)
        self.hide_empty_check.setToolTip("Link-layer keep-alive packets with no payload")
        self.hide_empty_check.toggled.connect(self._on_hide_empty)
        self.autoscroll_check = QCheckBox("Autoscroll")
        self.autoscroll_check.setChecked(True)
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.clicked.connect(self.clear)
        self.export_btn = QPushButton("Export…")
        self.export_btn.clicked.connect(self.export)
        self.count_label = QLabel()

        disp_row = QHBoxLayout()
        disp_row.addWidget(self.filter_edit, 1)
        disp_row.addWidget(self.hide_empty_check)
        disp_row.addWidget(self.autoscroll_check)
        disp_row.addWidget(self.clear_btn)
        disp_row.addWidget(self.export_btn)
        disp_row.addWidget(self.count_label)

        # Panes
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(20)
        self.table.setWordWrap(False)
        # Protocol colours come from BackgroundRole; keep the selection clearly visible.
        self.table.setStyleSheet("QTableView::item:selected { background: palette(highlight); "
                                 "color: palette(highlighted-text); }")
        hdr = self.table.horizontalHeader()
        hdr.setStretchLastSection(True)
        for col, w in ((COL_NO, 60), (COL_TIME, 95), (COL_SRC, 150), (COL_DST, 150),
                       (COL_PROTO, 65), (COL_LEN, 55)):
            hdr.resizeSection(col, w)
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.selectionModel().currentRowChanged.connect(self._on_row)

        self.details = QTreeWidget()
        self.details.setHeaderHidden(True)
        self.details.currentItemChanged.connect(self._on_detail)
        self.hex_view = QPlainTextEdit()
        self.hex_view.setReadOnly(True)
        self.hex_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.hex_view.setFont(mono)

        lower = QSplitter(Qt.Orientation.Horizontal)
        lower.addWidget(self.details)
        lower.addWidget(self.hex_view)
        lower.setSizes([500, 400])
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.table)
        split.addWidget(lower)
        split.setSizes([450, 250])

        layout = QVBoxLayout(self)
        layout.addLayout(src_row)
        layout.addLayout(disp_row)
        layout.addWidget(split, 1)

        self._current: CapturedPacket | None = None
        self.model.rowsInserted.connect(self._on_rows_inserted)
        self.model.modelReset.connect(self._on_model_reset)
        air.state_changed.connect(self._on_air_state)
        air.advertisers_changed.connect(self._adv_dirty)
        self._adv_timer = QTimer(self, singleShot=True, interval=ADV_REFRESH_MS)
        self._adv_timer.timeout.connect(self._refresh_follow)
        self.refresh_ports()
        self._refresh_follow()
        self._on_air_state(air.state)
        self._update_count()

    # -- sources --------------------------------------------------------------
    def _on_app_toggled(self, on: bool) -> None:
        self.app_capture.enabled = on

    def refresh_ports(self) -> None:
        current = self.port_combo.currentData()
        self.port_combo.clear()
        ports = list_ports()
        for p in ports:
            self.port_combo.addItem(p.label, p.device)
        if not ports:
            self.port_combo.addItem("No serial ports found", None)
        idx = self.port_combo.findData(current)
        if idx >= 0:
            self.port_combo.setCurrentIndex(idx)
        self._update_air_controls()

    def _toggle_sniffer(self) -> None:
        if self.air.running:
            self.air.stop()
            return
        port = self.port_combo.currentData()
        if port:
            self.air.start(port)

    def _on_air_state(self, state: str) -> None:
        text = {"idle": "idle", "opening": "opening…", "scanning": "scanning advertisements",
                "following": "following"}.get(state, state)
        if state == "following" and self.air.following:
            adv = self.air.advertisers.get(self.air.following)
            text = f"following {adv.name if adv and adv.name else self.air.following}"
        self.air_state.setText(f"Sniffer: {text}")
        self.sniff_btn.setText("Stop sniffer" if self.air.running else "Start sniffer")
        self._update_air_controls()

    def _update_air_controls(self) -> None:
        running = self.air.running
        self.port_combo.setEnabled(not running)
        self.refresh_ports_btn.setEnabled(not running)
        self.sniff_btn.setEnabled(running or bool(self.port_combo.currentData()))
        self.follow_combo.setEnabled(self.air.state in ("scanning", "following"))

    def _adv_dirty(self) -> None:
        if not self._adv_timer.isActive():
            self._adv_timer.start()

    def _refresh_follow(self) -> None:
        combo = self.follow_combo
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("All advertising (no connection)", SCAN_ALL)
        advs = sorted(self.air.advertisers.values(),
                      key=lambda a: (not a.connectable, not a.name, a.name.lower(), a.address))
        for a in advs:
            combo.addItem(a.label, a.address)
        idx = combo.findData(self.air.following) if self.air.following else 0
        combo.setCurrentIndex(max(idx, 0))
        combo.blockSignals(False)

    def _on_follow_chosen(self, index: int) -> None:
        target = self.follow_combo.itemData(index)
        if target == SCAN_ALL:
            self.air.scan()
        elif target:
            self.air.follow(target)
        self._on_air_state(self.air.state)

    # -- display ----------------------------------------------------------------
    def _on_hide_empty(self, on: bool) -> None:
        self.model.hide_empty = on
        self.model.rebuild()

    def _on_rows_inserted(self, *_):
        if self.autoscroll_check.isChecked():
            self.table.scrollToBottom()
        self._update_count()

    def _on_model_reset(self) -> None:
        self._update_count()
        if self._current is not None and self._current in self.model.rows:
            self.table.selectRow(self.model.rows.index(self._current))
        else:
            self._show_packet(None)

    def _update_count(self) -> None:
        total, shown = len(self.store.packets), len(self.model.rows)
        self.count_label.setText(f"{shown:,} / {total:,} packets" if shown != total else f"{total:,} packets")

    def clear(self) -> None:
        self.store.clear()

    def _on_row(self, current: QModelIndex, _previous=None) -> None:
        self._show_packet(self.model.packet(current.row()) if current.isValid() else None)

    def _show_packet(self, p: CapturedPacket | None) -> None:
        self._current = p
        self.details.clear()
        self.hex_view.setExtraSelections([])
        if p is None:
            self.hex_view.setPlainText("")
            return
        summary = QTreeWidgetItem([f"Packet {p.number}: {p.length} bytes, {p.iface}, {p.protocol}"])
        self.details.addTopLevelItem(summary)
        for f in p.tree:
            self.details.addTopLevelItem(self._item(f))
        for i in range(self.details.topLevelItemCount()):
            item = self.details.topLevelItem(i)
            item.setExpanded(i == self.details.topLevelItemCount() - 1 or item.childCount() < 6)
        self.hex_view.setPlainText(hex_dump(p.raw))

    def _item(self, f: Field) -> QTreeWidgetItem:
        item = QTreeWidgetItem([f"{f.label}: {f.value}" if f.value else f.label])
        item.setData(0, SPAN_ROLE, f.span)
        item.setToolTip(0, item.text(0))
        for c in f.children:
            item.addChild(self._item(c))
        return item

    def _on_detail(self, item: QTreeWidgetItem | None, _prev=None) -> None:
        span = item.data(0, SPAN_ROLE) if item is not None else None
        self.highlight(span)

    def highlight(self, span) -> None:
        if not span or self._current is None:
            self.hex_view.setExtraSelections([])
            return
        start, end = max(0, span[0]), min(len(self._current.raw), span[1])
        fmt = QTextCharFormat()
        fmt.setBackground(self.palette().highlight())
        fmt.setForeground(self.palette().highlightedText())
        sels = []
        doc = self.hex_view.document()
        for i in range(start, end):
            hx, asc = hex_positions(i)
            for pos, n in ((hx, 2), (asc, 1)):
                sel = QTextEdit.ExtraSelection()
                cur = QTextCursor(doc)
                cur.setPosition(pos)
                cur.setPosition(pos + n, QTextCursor.MoveMode.KeepAnchor)
                sel.cursor, sel.format = cur, fmt
                sels.append(sel)
        self.hex_view.setExtraSelections(sels)
        if sels:
            cur = QTextCursor(doc)
            cur.setPosition(hex_positions(start)[0])
            self.hex_view.setTextCursor(cur)
            self.hex_view.ensureCursorVisible()

    # -- export -----------------------------------------------------------------
    def export(self) -> None:
        path, chosen = QFileDialog.getSaveFileName(
            self, "Export packets", "capture.pcap",
            "Wireshark capture, on-air packets (*.pcap);;CSV, shown packets (*.csv)")
        if not path:
            return
        try:
            if path.lower().endswith(".csv") or chosen.startswith("CSV"):
                n = self.export_csv(path)
            else:
                n = pcap.write(path, self.model.rows, self.store.epoch)
                if n == 0:
                    QMessageBox.information(self, "Export", "There are no on-air packets in the list; "
                                            "the pcap file only holds sniffer captures. Use CSV for app traffic.")
                    return
        except OSError as exc:
            QMessageBox.warning(self, "Export failed", str(exc))
            return
        win = self.window()
        if hasattr(win, "show_status"):
            win.show_status(f"Exported {n} packets to {path}")

    def export_csv(self, path: str) -> int:
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(COLUMNS + ["Interface", "Bytes"])
            for p in self.model.rows:
                w.writerow([p.number, f"{p.time:.6f}", p.source, p.destination, p.protocol,
                            p.length, p.info, p.iface, p.raw.hex(" ")])
        return len(self.model.rows)
