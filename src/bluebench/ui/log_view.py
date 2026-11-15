"""Log console: filterable, colour-coded, auto-scrolling, exportable."""
from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QHBoxLayout,
                               QHeaderView, QLineEdit, QMessageBox, QPushButton, QTableView,
                               QVBoxLayout, QWidget)

from ..core.log_bus import LEVELS, LogBus, LogEntry, export, level_rank
from .qt_compat import filter_change

FLUSH_MS = 100
COL_TIME, COL_LEVEL, COL_SOURCE, COL_MESSAGE = range(4)
HEADERS = ["Time", "Level", "Source", "Message"]
LEVEL_COLORS = {
    "debug": QColor(130, 130, 130),
    "warning": QColor("#e65100"),
    "error": QColor("#c62828"),
}
ALL_SOURCES = "All sources"


class LogModel(QAbstractTableModel):
    """Mirrors the LogBus, inserting new entries in batches."""

    def __init__(self, bus: LogBus, parent=None) -> None:
        super().__init__(parent)
        self.bus = bus
        self._rows: list[LogEntry] = list(bus.entries)
        self._pending: list[LogEntry] = []
        bus.entry_added.connect(self._on_entry)
        bus.cleared.connect(self._on_cleared)

    def _on_entry(self, entry: LogEntry) -> None:
        self._pending.append(entry)

    def flush(self) -> bool:
        """Insert pending entries; returns True if rows were added."""
        if not self._pending:
            return False
        batch, self._pending = self._pending, []
        start = len(self._rows)
        self.beginInsertRows(QModelIndex(), start, start + len(batch) - 1)
        self._rows.extend(batch)
        self.endInsertRows()
        excess = len(self._rows) - self.bus.max_entries
        if excess > 0:
            self.beginRemoveRows(QModelIndex(), 0, excess - 1)
            del self._rows[:excess]
            self.endRemoveRows()
        return True

    def _on_cleared(self) -> None:
        self.beginResetModel()
        self._rows.clear()
        self._pending.clear()
        self.endResetModel()

    def entry(self, row: int) -> LogEntry:
        return self._rows[row]

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
        e = self._rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return (e.time_text, e.level.upper(), e.source, e.message)[index.column()]
        if role == Qt.ItemDataRole.ForegroundRole and e.level in LEVEL_COLORS:
            return QBrush(LEVEL_COLORS[e.level])
        if role == Qt.ItemDataRole.ToolTipRole and index.column() == COL_MESSAGE:
            return e.message
        return None


class LogFilterProxy(QSortFilterProxyModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.min_level = "info"
        self.source: str | None = None
        self.text = ""

    def set_filter(self, *, min_level: str | None = None, source: str | None | bool = False,
                   text: str | None = None) -> None:
        with filter_change(self):
            if min_level is not None:
                self.min_level = min_level
            if source is not False:
                self.source = source  # type: ignore[assignment]
            if text is not None:
                self.text = text.lower()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        e: LogEntry = self.sourceModel().entry(source_row)  # type: ignore[attr-defined]
        if level_rank(e.level) < level_rank(self.min_level):
            return False
        if self.source and e.source != self.source:
            return False
        return not self.text or self.text in e.message.lower() or self.text in e.source.lower()


class LogView(QWidget):
    def __init__(self, bus: LogBus, parent=None) -> None:
        super().__init__(parent)
        self.bus = bus
        self.model = LogModel(bus, self)
        self.proxy = LogFilterProxy(self)
        self.proxy.setSourceModel(self.model)

        self.level_combo = QComboBox()
        for lvl in LEVELS:
            self.level_combo.addItem(f"{lvl.capitalize()}+" if lvl != "error" else "Error", lvl)
        self.level_combo.setCurrentIndex(LEVELS.index("info"))
        self.level_combo.setToolTip("Minimum level shown")
        self.level_combo.currentIndexChanged.connect(
            lambda _: self.proxy.set_filter(min_level=self.level_combo.currentData()))
        self.source_combo = QComboBox()
        self.source_combo.addItem(ALL_SOURCES, None)
        self.source_combo.setMinimumContentsLength(18)
        self.source_combo.currentIndexChanged.connect(
            lambda _: self.proxy.set_filter(source=self.source_combo.currentData()))
        self.search = QLineEdit(placeholderText="Search log…")
        self.search.textChanged.connect(lambda t: self.proxy.set_filter(text=t))
        self.autoscroll = QCheckBox("Auto-scroll")
        self.autoscroll.setChecked(True)
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(bus.clear)
        export_btn = QPushButton("Export…")
        export_btn.clicked.connect(self._export_dialog)

        bar = QHBoxLayout()
        bar.addWidget(self.level_combo)
        bar.addWidget(self.source_combo)
        bar.addWidget(self.search, 1)
        bar.addWidget(self.autoscroll)
        bar.addWidget(clear_btn)
        bar.addWidget(export_btn)

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(20)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setWordWrap(False)
        self.table.setShowGrid(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(COL_MESSAGE, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(COL_TIME, 95)
        self.table.setColumnWidth(COL_LEVEL, 70)
        self.table.setColumnWidth(COL_SOURCE, 240)
        QShortcut(QKeySequence.StandardKey.Copy, self.table, activated=self.copy_selected)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(bar)
        layout.addWidget(self.table)

        bus.cleared.connect(self._reset_sources)
        self._timer = QTimer(self, interval=FLUSH_MS)
        self._timer.timeout.connect(self.flush)
        self._timer.start()

    def flush(self) -> None:
        if not self.model.flush():
            return
        known = {self.source_combo.itemText(i) for i in range(1, self.source_combo.count())}
        for src in self.bus.sources():
            if src not in known:
                self.source_combo.addItem(src, src)
        if self.autoscroll.isChecked():
            self.table.scrollToBottom()

    def _reset_sources(self) -> None:
        self.source_combo.setCurrentIndex(0)
        while self.source_combo.count() > 1:
            self.source_combo.removeItem(1)

    def visible_entries(self) -> list[LogEntry]:
        return [self.model.entry(self.proxy.mapToSource(self.proxy.index(r, 0)).row())
                for r in range(self.proxy.rowCount())]

    def copy_selected(self) -> None:
        rows = sorted(i.row() for i in self.table.selectionModel().selectedRows())
        lines = [self.model.entry(self.proxy.mapToSource(self.proxy.index(r, 0)).row()).as_line()
                 for r in rows]
        if lines:
            QGuiApplication.clipboard().setText("\n".join(lines))

    def _export_dialog(self) -> None:
        self.flush()
        path, _ = QFileDialog.getSaveFileName(
            self, "Export log", "bluebench-log.txt",
            "Text files (*.txt);;CSV files (*.csv);;All files (*)")
        if not path:
            return
        filtered = self.proxy.rowCount() != self.model.rowCount()
        entries = self.visible_entries() if filtered else list(self.bus.entries)
        try:
            export(entries, path)
        except OSError as exc:
            QMessageBox.warning(self, "Export failed", str(exc))
            return
        self.bus.log("info", "App", f"Exported {len(entries)} log entries to {path}")
