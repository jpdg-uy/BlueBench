"""Scanner panel: live device table, filters and advertisement details."""
from __future__ import annotations

import asyncio
import time

from PySide6.QtCore import (QAbstractTableModel, QEvent, QModelIndex, QObject, QRect,
                            QSortFilterProxyModel, Qt, QTimer, Signal)
from PySide6.QtGui import QBrush, QColor, QFont, QGuiApplication, QPainter
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QMenu, QPushButton, QSpinBox, QSplitter,
                               QStyledItemDelegate, QStyleOptionViewItem, QTableView,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..ble import pairing
from ..ble import registry
from ..ble.adv_filter import byte_pattern, field_terms, manufacturer_matches
from ..ble.adv_parser import AdvField
from ..ble.scanner import KIND_TEXT, PRIVATE_KINDS, DeviceEntry, Scanner
from .qt_compat import filter_change

REFRESH_MS = 250
# Rows only change places at this pace, by averaged RSSI (see DeviceEntry.smoothed_rssi).
RESORT_MS = 2000
# A device shown by the Min RSSI filter is hidden only once its average drops this far below.
RSSI_HYSTERESIS_DB = 3
BOND_REFRESH_MS = 10_000
PIN = "📌 "
STALE_AFTER_S = 10.0
SORT_ROLE = Qt.ItemDataRole.UserRole + 1
RSSI_ROLE = Qt.ItemDataRole.UserRole + 2   # latest RSSI, for the signal bar

COL_NAME, COL_ADDRESS, COL_BOND, COL_RSSI, COL_INTERVAL, COL_INFO = range(6)
HEADERS = ["Name", "Address", "Bond", "RSSI", "Interval", "Info"]
BOND_TIP = ("Bonded (paired) with this PC. A device that advertises with a changing "
            "private address (RPA) is recognised once it has been bonded from BlueBench.")


class DeviceTableModel(QAbstractTableModel):
    """Rows are device keys (first address seen) in discovery order; data is read
    from the scanner. A device that changes its private address keeps its row."""

    def __init__(self, scanner: Scanner, parent=None) -> None:
        super().__init__(parent)
        self._scanner = scanner
        self._rows: list[str] = []
        self._row_of: dict[str, int] = {}
        self._pending_new: list[str] = []
        self._pending_set: set[str] = set()
        self.pinned: set[str] = set()      # keys kept at the top of the list
        self.bonded: set[str] = set()      # addresses Windows has a bond for
        # RSSI / interval used for sorting, refreshed every RESORT_MS (not per packet).
        self._sort_keys: dict[str, tuple[int, float]] = {}
        scanner.device_updated.connect(self._on_device_updated)

    # The scanner can fire hundreds of callbacks per second; new rows are
    # queued here and inserted in one batch per refresh tick.
    def _on_device_updated(self, address: str) -> None:
        if address not in self._row_of and address not in self._pending_set:
            self._pending_new.append(address)
            self._pending_set.add(address)

    def flush(self) -> None:
        if self._pending_new:
            start = len(self._rows)
            self.beginInsertRows(QModelIndex(), start, start + len(self._pending_new) - 1)
            for addr in self._pending_new:
                self._row_of[addr] = len(self._rows)
                self._rows.append(addr)
            self.endInsertRows()
            self._pending_new.clear()
            self._pending_set.clear()
        # Repaint everything once per tick: cheap, and keeps "stale" greying current.
        if self._rows:
            self.dataChanged.emit(self.index(0, 0),
                                  self.index(len(self._rows) - 1, len(HEADERS) - 1))

    def refresh_sort_keys(self) -> None:
        """Take a new snapshot of the values rows are sorted by."""
        now = time.monotonic()
        self._sort_keys = {a: self._keys_of(self._scanner.devices[a], now)
                           for a in self._rows if a in self._scanner.devices}

    @staticmethod
    def _keys_of(e: DeviceEntry, now: float | None = None) -> tuple[int, float]:
        return e.smoothed_rssi(now=now), e.interval_ms() or 1e9

    def sort_keys(self, e: DeviceEntry) -> tuple[int, float]:
        keys = self._sort_keys.get(e.key)
        if keys is None:  # new since the last snapshot
            keys = self._sort_keys[e.key] = self._keys_of(e)
        return keys

    def reset(self) -> None:
        self.beginResetModel()
        self._sort_keys.clear()
        self._rows.clear()
        self._row_of.clear()
        self._pending_new.clear()
        self._pending_set.clear()
        self.endResetModel()

    def entry(self, row: int) -> DeviceEntry:
        return self._scanner.devices[self._rows[row]]

    def is_bonded(self, e: DeviceEntry | str) -> bool:
        if isinstance(e, str):
            return e.upper() in self.bonded
        if e.identity_address and e.identity_address.upper() in self.bonded:
            return True
        return any(a.upper() in self.bonded for a in e.addresses)

    def is_pinned_row(self, row: int) -> bool:
        return self._rows[row] in self.pinned

    # -- Qt model API ---------------------------------------------------------
    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return HEADERS[section]
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        e = self.entry(index.row())
        col = index.column()
        pinned = e.key in self.pinned
        if role == Qt.ItemDataRole.DisplayRole:
            if col == COL_NAME:
                return PIN + e.display_name if pinned else e.display_name
            if col == COL_ADDRESS:
                return address_text(e)
            if col == COL_BOND:
                return "🔒" if self.is_bonded(e) else ""
            if col == COL_RSSI:
                return f"{e.rssi} dBm"
            if col == COL_INTERVAL:
                iv = e.interval_ms()
                return f"{iv:.0f} ms" if iv is not None else ""
            if col == COL_INFO:
                return _info_text(e)
        if role == SORT_ROLE:
            if col == COL_RSSI:
                return self.sort_keys(e)[0]
            if col == COL_INTERVAL:
                return self.sort_keys(e)[1]
            if col == COL_NAME:
                return e.display_name.lower()
            if col == COL_BOND:
                return 0 if self.is_bonded(e) else 1
            return (self.data(index) or "").lower()
        if role == Qt.ItemDataRole.FontRole and pinned and col == COL_NAME:
            f = QFont()
            f.setBold(True)
            return f
        if role == Qt.ItemDataRole.TextAlignmentRole and col == COL_BOND:
            return Qt.AlignmentFlag.AlignCenter
        if role == Qt.ItemDataRole.ToolTipRole and col == COL_BOND and self.is_bonded(e):
            return BOND_TIP
        if role == Qt.ItemDataRole.ToolTipRole and col == COL_ADDRESS:
            return address_tip(e)
        if role == Qt.ItemDataRole.ToolTipRole and col == COL_RSSI:
            return f"Average over the last few seconds: {e.smoothed_rssi()} dBm"
        if role == Qt.ItemDataRole.ForegroundRole and _is_stale(e):
            return QBrush(QColor(140, 140, 140))
        if role == Qt.ItemDataRole.UserRole:
            return e.key
        if role == RSSI_ROLE:
            return e.rssi
        return None


def address_text(e: DeviceEntry) -> str:
    """The current address; a private one is marked, with how many it has used."""
    text = e.address
    if e.address_kind in PRIVATE_KINDS:
        text += "  RPA" if e.address_kind == "rpa" else "  NRPA"
    if len(e.addresses) > 1:
        text += f"  ↻{len(e.addresses)}"
    return text


def address_tip(e: DeviceEntry) -> str:
    lines = [f"{e.address}: {KIND_TEXT.get(e.address_kind or '', 'address type not reported')}"]
    if len(e.addresses) > 1:
        span = e.last_seen - min(e.first_heard.values())
        how = ("same identity in the advertising data" if e.joined == "identity"
               else "a new private address appeared as the previous one went quiet "
                    "(same name, data and signal)")
        lines.append(f"{len(e.addresses)} addresses in {span:.0f} s, joined because {how}:")
        lines += [f"  {a}" for a in reversed(list(e.addresses))][:10]
    return "\n".join(lines)


def _is_stale(e: DeviceEntry) -> bool:
    return time.monotonic() - e.last_seen > STALE_AFTER_S


def _info_text(e: DeviceEntry) -> str:
    parts = []
    if e.adv.beacon:
        parts.append(e.adv.beacon)
    for f in e.adv.fields:
        if f.label == "Manufacturer Data":
            parts.append(f.value.split(" <")[0])
    if e.adv.service_uuids:
        parts.append(f"{len(e.adv.service_uuids)} service(s)")
    return " · ".join(dict.fromkeys(parts))


class DeviceFilterProxy(QSortFilterProxyModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.text = ""
        self.hex = None  # the text as a byte pattern, for manufacturer data
        self.terms: tuple[tuple[str, str], ...] | None = None  # "model:0B" filters
        self.min_rssi = -100
        self.named_only = False
        self.natural = True  # discovery order until a header is clicked
        self._rssi_shown: set[str] = set()  # passed the Min RSSI filter, kept with hysteresis
        self.setSortRole(SORT_ROLE)

    def sort(self, column: int, order=Qt.SortOrder.AscendingOrder) -> None:
        # Always keep a sort active so pinned rows float up even in
        # discovery order; column -1 means "discovery order".
        self.natural = column < 0
        super().sort(0 if self.natural else column,
                     Qt.SortOrder.AscendingOrder if self.natural else order)

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        model: DeviceTableModel = self.sourceModel()  # type: ignore[assignment]
        lp, rp = model.is_pinned_row(left.row()), model.is_pinned_row(right.row())
        if lp != rp:
            # Pinned first in either direction (Qt reverses the result for descending).
            return lp if self.sortOrder() == Qt.SortOrder.AscendingOrder else rp
        if self.natural:
            return left.row() < right.row()
        lv, rv = left.data(SORT_ROLE), right.data(SORT_ROLE)
        if lv == rv:
            return left.row() < right.row()  # ties keep a fixed order (discovery)
        return super().lessThan(left, right)

    def _passes_rssi(self, e: DeviceEntry) -> bool:
        if self.min_rssi <= -100:
            return True
        rssi = e.smoothed_rssi()
        margin = RSSI_HYSTERESIS_DB if e.key in self._rssi_shown else 0
        if rssi >= self.min_rssi - margin:
            self._rssi_shown.add(e.key)
            return True
        self._rssi_shown.discard(e.key)
        return False

    def forget(self) -> None:
        """Drop per-device filter state (after the list is cleared)."""
        self._rssi_shown.clear()

    def set_filter(self, *, text: str | None = None, min_rssi: int | None = None,
                   named_only: bool | None = None) -> None:
        with filter_change(self):
            if text is not None:
                self.text = text.strip().lower()
                self.hex = byte_pattern(text)
                self.terms = field_terms(text)
            if min_rssi is not None:
                self.min_rssi = min_rssi
                self._rssi_shown.clear()
            if named_only is not None:
                self.named_only = named_only

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        model: DeviceTableModel = self.sourceModel()  # type: ignore[assignment]
        e = model.entry(source_row)
        if not self._passes_rssi(e):
            return False
        if self.named_only and not e.name:
            return False
        if self.terms:
            fields = registry.filter_fields()
            return all(fields[n].match(e.adv, fields[n].parse(v)) for n, v in self.terms)
        if self.text:
            if self.hex and manufacturer_matches(e.adv.manufacturer_data, self.hex):
                return True
            haystack = f"{e.display_name} {' '.join(e.addresses)} {_info_text(e)}".lower()
            return self.text in haystack
        return True


class RssiDelegate(QStyledItemDelegate):
    """Draws a small signal-strength bar next to the RSSI value."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        super().paint(painter, option, index)
        rssi = index.data(RSSI_ROLE)
        if not isinstance(rssi, int):
            return
        level = max(0.0, min(1.0, (rssi + 100) / 60))  # -100 dBm .. -40 dBm
        r = option.rect
        bar = QRect(r.right() - 54, r.center().y() - 4, 48, 8)
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(128, 128, 128, 60))
        painter.drawRoundedRect(bar, 3, 3)
        color = QColor("#2e7d32") if level > 0.6 else QColor("#f9a825") if level > 0.3 else QColor("#c62828")
        painter.setBrush(color)
        painter.drawRoundedRect(QRect(bar.left(), bar.top(), int(bar.width() * level), bar.height()), 3, 3)
        painter.restore()


class ScannerView(QWidget):
    connect_requested = Signal(str)  # address

    def __init__(self, scanner: Scanner, parent=None) -> None:
        super().__init__(parent)
        self.scanner = scanner
        self.model = DeviceTableModel(scanner, self)
        self.proxy = DeviceFilterProxy(self)
        self.proxy.setSourceModel(self.model)

        # toolbar
        self.scan_btn = QPushButton("Start scan")
        self.scan_btn.setCheckable(True)
        self.scan_btn.clicked.connect(self._toggle_scan)
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self._clear)
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setEnabled(False)
        self.connect_btn.clicked.connect(self._connect_selected)
        self.filter_edit = QLineEdit(placeholderText="Filter by name, address, manufacturer, data (hex)…")
        self.filter_edit.setToolTip(
            "Text matches the name, address and manufacturer.\n"
            "Hex bytes (e.g. 5900, 0x5900 or 59 00) also match manufacturer data that\n"
            "starts with them, as sent on air (company ID first, low byte first)\n"
            "or with the company ID written as usual (0x0059 → 0059…).\n"
            "Wildcards: ? is any hex digit (?? any byte), * any number of bytes.\n"
            "0059 ?? 0B skips the byte after the company ID; *0B finds 0B anywhere.")
        fields = registry.filter_fields()
        if fields:
            self.filter_edit.setToolTip(
                self.filter_edit.toolTip() + "\nDecoded data: " + ", ".join(
                    f"{n}:{ff.metavar}" for n, ff in fields.items())
                + " (several separated by spaces).")
        self.filter_edit.textChanged.connect(lambda t: self.proxy.set_filter(text=t))
        self.rssi_spin = QSpinBox(minimum=-100, maximum=0, value=-100, suffix=" dBm")
        self.rssi_spin.setToolTip("Hide devices weaker than this RSSI")
        self.rssi_spin.valueChanged.connect(lambda v: self.proxy.set_filter(min_rssi=v))
        named = QCheckBox("Named only")
        named.toggled.connect(lambda on: self.proxy.set_filter(named_only=on))
        self.join_check = QCheckBox("Join RPAs", checked=scanner.follow_rotation)
        self.join_check.setToolTip(
            "Keep one row for a device that changes its private address (RPA):\n"
            "a new private address that appears as one with the same name, data and\n"
            "signal goes quiet is taken as the same device. Applies to new addresses.")
        self.join_check.toggled.connect(lambda on: setattr(scanner, "follow_rotation", on))
        self.count_label = QLabel("0 devices")
        # Fixed width, right-aligned: the bar must not move as the counts change.
        self.count_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.count_label.setMinimumWidth(
            self.count_label.fontMetrics().horizontalAdvance("8888 / 8888 devices") + 8)
        self.count_label.setToolTip("Shown / all devices. While the pointer is over the list "
                                    "the rows keep their places;\nthey re-sort when it leaves.")

        bar = QHBoxLayout()
        for w in (self.scan_btn, clear_btn, self.connect_btn):
            bar.addWidget(w)
        bar.addWidget(self.filter_edit, 1)
        bar.addWidget(QLabel("Min RSSI"))
        bar.addWidget(self.rssi_spin)
        bar.addWidget(named)
        bar.addWidget(self.join_check)
        bar.addWidget(self.count_label)

        # table
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setSortingEnabled(True)
        self.proxy.sort(-1)  # discovery order until the user clicks a header
        self.table.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        self.table.verticalHeader().hide()
        self.table.setItemDelegateForColumn(COL_RSSI, RssiDelegate(self.table))
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(COL_INFO, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(COL_NAME, 200)
        self.table.setColumnWidth(COL_ADDRESS, 150)
        self.table.setColumnWidth(COL_RSSI, 140)
        self.table.setColumnWidth(COL_BOND, 50)
        self.table.setColumnHidden(COL_BOND, True)  # shown once the OS reports bonds
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.selectionModel().selectionChanged.connect(self._on_selection)
        self.table.doubleClicked.connect(lambda _: self._connect_selected())

        # details
        self.details = QTreeWidget()
        self.details.setHeaderLabels(["Field", "Value"])
        self.details.setColumnWidth(0, 200)

        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.table)
        split.addWidget(self.details)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)

        layout = QVBoxLayout(self)
        layout.addLayout(bar)
        layout.addWidget(split)

        self._details_sig: tuple | None = None
        self._timer = QTimer(self, interval=REFRESH_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self._resort_timer = QTimer(self, interval=RESORT_MS)
        self._resort_timer.timeout.connect(self.model.refresh_sort_keys)
        self._resort_timer.start()
        # Rows keep their places while the pointer is over the list, so they do not
        # move under the click. Values keep updating; new devices go to the end.
        self.order_paused = False
        self._hover = _HoverWatcher(self.set_order_paused, self)
        self.table.viewport().installEventFilter(self._hover)
        scanner.scanning_changed.connect(self._on_scanning_changed)
        self._bond_timer = QTimer(self, interval=BOND_REFRESH_MS)
        self._bond_timer.timeout.connect(self.request_bond_refresh)
        self._bond_timer.start()
        self._bond_task: asyncio.Future | None = None
        QTimer.singleShot(0, self.request_bond_refresh)

    # -- actions ----------------------------------------------------------------
    def _toggle_scan(self, checked: bool) -> None:
        asyncio.ensure_future(self.scanner.start() if checked else self.scanner.stop())

    def _on_scanning_changed(self, on: bool) -> None:
        self.scan_btn.setChecked(on)
        self.scan_btn.setText("Stop scan" if on else "Start scan")

    def set_order_paused(self, paused: bool) -> None:
        if paused == self.order_paused:
            return
        self.order_paused = paused
        self.proxy.setDynamicSortFilter(not paused)
        if not paused:
            self.proxy.invalidate()  # catch up: sort and filter with the latest values
        self._update_count()

    def _update_count(self) -> None:
        # The pause is not shown in the bar: changing the text there moved the buttons.
        self.count_label.setText(f"{self.proxy.rowCount()} / {self.model.rowCount()} devices")

    def _clear(self) -> None:
        self.scanner.clear()
        self.proxy.forget()
        self.model.reset()
        self.details.clear()
        self._details_sig = None

    # -- pinning & bonds --------------------------------------------------------
    def set_pinned(self, address: str, pinned: bool) -> None:
        (self.model.pinned.add if pinned else self.model.pinned.discard)(address)
        self.proxy.invalidate()  # re-sort now, not at the next update

    def request_bond_refresh(self) -> None:
        if self._bond_task is None or self._bond_task.done():
            self._bond_task = asyncio.ensure_future(self.refresh_bonds())

    async def refresh_bonds(self) -> None:
        bonded = await pairing.bonded_addresses()
        if bonded is None:
            return  # platform cannot tell: keep the column hidden
        self.model.bonded = bonded
        self.table.setColumnHidden(COL_BOND, False)
        self.model.flush()

    def _context_menu(self, pos) -> None:
        index = self.table.indexAt(pos)
        if not index.isValid():
            return
        self.table.selectRow(index.row())
        addr = self.selected_address()
        if addr is None:
            return
        menu = QMenu(self)
        menu.addAction("Connect", self._connect_selected)
        if addr in self.model.pinned:
            menu.addAction("Unpin", lambda: self.set_pinned(addr, False))
        else:
            menu.addAction("Pin to top", lambda: self.set_pinned(addr, True))
        menu.addSeparator()
        current = self.scanner.devices[addr].address if addr in self.scanner.devices else addr
        menu.addAction("Copy address", lambda: QGuiApplication.clipboard().setText(current))
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def selected_address(self) -> str | None:
        rows = self.table.selectionModel().selectedRows()
        return rows[0].data(Qt.ItemDataRole.UserRole) if rows else None

    def _connect_selected(self) -> None:
        addr = self.selected_address()
        if addr:
            self.connect_requested.emit(addr)

    def _on_selection(self, *_):
        self.connect_btn.setEnabled(self.selected_address() is not None)
        self._details_sig = None
        self._refresh_details()

    # -- periodic refresh ---------------------------------------------------
    def _tick(self) -> None:
        self.model.flush()
        self._update_count()
        self._refresh_details()

    def _refresh_details(self) -> None:
        addr = self.selected_address()
        e = self.scanner.devices.get(addr) if addr else None
        if e is None:
            if self._details_sig is not None:
                self.details.clear()
                self._details_sig = None
            return
        sig = (e.key, e.address, e.adv_count)
        if sig == self._details_sig:
            return
        self._details_sig = sig
        self.details.clear()
        iv = e.interval_ms()
        rows = [
            AdvField("Address", e.address),
            AdvField("Address type", KIND_TEXT.get(e.address_kind or "", "not reported")),
            *([AdvField("Addresses used", str(len(e.addresses)),
                        [AdvField(a, "current" if a == e.address else "")
                         for a in reversed(list(e.addresses))])]
              if len(e.addresses) > 1 else []),
            AdvField("RSSI", f"{e.rssi} dBm"),
            AdvField("Packets", str(e.adv_count)),
            AdvField("Avg. interval", f"{iv:.0f} ms" if iv else "—"),
            *e.adv.fields,
        ]
        for f in rows:
            self.details.addTopLevelItem(_tree_item(f))
        self.details.expandAll()


class _HoverWatcher(QObject):
    """Calls ``on_change(True)`` when the pointer enters a widget, ``False`` when it leaves."""

    def __init__(self, on_change, parent=None) -> None:
        super().__init__(parent)
        self._on_change = on_change

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.Enter:
            self._on_change(True)
        elif event.type() == QEvent.Type.Leave:
            self._on_change(False)
        return False


def _tree_item(f: AdvField) -> QTreeWidgetItem:
    item = QTreeWidgetItem([f.label, f.value])
    item.setToolTip(1, f.value)
    for child in f.children:
        item.addChild(_tree_item(child))
    return item
