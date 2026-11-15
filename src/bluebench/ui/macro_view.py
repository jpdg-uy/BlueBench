"""Macros tab: record, edit and run sequences of GATT operations."""
from __future__ import annotations

import copy
import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMenu, QMessageBox, QPushButton, QSpinBox,
                               QSplitter, QToolButton, QVBoxLayout, QWidget)

from ..ble import uuids
from ..ble.formats import ValueFormat, encode, to_hex
from ..core.macros import OPS, Macro, MacroLibrary, MacroRunner, Step, dumps, loads

OP_LABELS = {
    "write": "Write", "read": "Read", "subscribe": "Subscribe", "unsubscribe": "Unsubscribe",
    "expect": "Expect notification", "wait": "Wait",
}
OK_COLOR, FAIL_COLOR = QColor("#2e7d32"), QColor("#c62828")


class StepDialog(QDialog):
    """Create or edit one step."""

    def __init__(self, step: Step, characteristics: list[tuple[str, str]],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Macro step")
        self.setMinimumWidth(480)
        self.step = copy.copy(step)

        self.op_combo = QComboBox()
        for op in OPS:
            self.op_combo.addItem(OP_LABELS[op], op)
        self.op_combo.setCurrentIndex(OPS.index(step.op))
        self.char_combo = QComboBox(editable=True)
        self.char_combo.lineEdit().setPlaceholderText("UUID, e.g. 2A37 or 6E400002-…")
        for uuid, label in characteristics:
            self.char_combo.addItem(label, uuid)
        self._set_char(step.uuid)
        self.data_format = QComboBox()
        self.data_format.addItems(["Hex", "Text"])
        self.data_edit = QLineEdit(step.data)
        data_row = QHBoxLayout()
        data_row.addWidget(self.data_edit, 1)
        data_row.addWidget(self.data_format)
        self.response = QCheckBox("With response (request)")
        self.response.setChecked(step.response)
        self.ms = QSpinBox(minimum=0, maximum=3_600_000, singleStep=100, suffix=" ms")
        self.ms.setValue(step.ms)
        self.error = QLabel()
        self.error.setStyleSheet("color:#c62828")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.form = QFormLayout(self)
        self.form.addRow("Step", self.op_combo)
        self.form.addRow("Characteristic", self.char_combo)
        self.form.addRow("Value", data_row)
        self.form.addRow("", self.response)
        self.form.addRow("Time", self.ms)
        self.form.addRow(self.error)
        self.form.addRow(buttons)
        self.op_combo.currentIndexChanged.connect(self._update_fields)
        self._update_fields()

    def _set_char(self, uuid: str) -> None:
        if not uuid:
            return
        idx = self.char_combo.findData(uuids.normalize(uuid))
        if idx >= 0:
            self.char_combo.setCurrentIndex(idx)
        else:
            self.char_combo.setEditText(uuid)

    def _uuid(self) -> str:
        text = self.char_combo.currentText().strip()
        idx = self.char_combo.currentIndex()
        if idx >= 0 and self.char_combo.itemText(idx) == text:
            return self.char_combo.itemData(idx)
        return uuids.normalize(text) if text else ""

    def _update_fields(self) -> None:
        op = self.op_combo.currentData()
        # Rows: 0 step, 1 characteristic, 2 value, 3 response, 4 time
        self.form.setRowVisible(1, op != "wait")
        self.form.setRowVisible(2, op in ("write", "read", "expect"))
        self.form.setRowVisible(3, op == "write")
        self.form.setRowVisible(4, op in ("wait", "expect"))
        self.data_format.setVisible(op == "write")
        if op == "write":
            self.data_edit.setPlaceholderText("Bytes to write, e.g. 01 02 FF")
        else:
            self.data_edit.setPlaceholderText("Expected value (optional), ?? = any byte, e.g. 01 ?? FF")
        if op == "expect" and self.ms.value() == 0:
            self.ms.setValue(2000)

    def build(self) -> Step:
        op = self.op_combo.currentData()
        data = self.data_edit.text().strip()
        if op == "write" and self.data_format.currentText() == "Text":
            data = to_hex(encode(self.data_edit.text(), ValueFormat.UTF8), " ")
        elif op == "write" and data:
            data = to_hex(encode(data, ValueFormat.HEX), " ")  # normalise spelling
        step = Step(op, uuid="" if op == "wait" else self._uuid(),
                    data=data if op in ("write", "read", "expect") else "",
                    response=self.response.isChecked() if op == "write" else True,
                    ms=self.ms.value() if op in ("wait", "expect") else 0)
        step.validate()
        return step

    def accept(self) -> None:
        try:
            self.step = self.build()
        except ValueError as exc:
            self.error.setText(str(exc))
            return
        super().accept()


class MacroView(QWidget):
    def __init__(self, session, library: MacroLibrary, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self.library = library
        self.runner = MacroRunner(session, self)
        self.recording = False
        self._step_status: dict[int, bool] = {}
        self._current_step: int | None = None

        # macro list
        self.macro_list = QListWidget()
        self.macro_list.currentRowChanged.connect(lambda _: self._show_steps())
        self.macro_list.itemChanged.connect(self._renamed)
        new_btn = QPushButton("New")
        new_btn.clicked.connect(self.new_macro)
        more = QToolButton(text="More")
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self)
        self.dup_action = menu.addAction("Duplicate", self.duplicate_macro)
        self.rename_action = menu.addAction("Rename", self._start_rename)
        self.delete_action = menu.addAction("Delete", self.delete_macro)
        menu.addSeparator()
        menu.addAction("Import…", self._import_dialog)
        self.export_action = menu.addAction("Export…", self._export_dialog)
        more.setMenu(menu)
        list_buttons = QHBoxLayout()
        list_buttons.addWidget(new_btn)
        list_buttons.addWidget(more)
        list_buttons.addStretch(1)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("<b>Macros</b>"))
        left_layout.addWidget(self.macro_list, 1)
        left_layout.addLayout(list_buttons)

        # steps
        self.step_list = QListWidget()
        self.step_list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.step_list.model().rowsMoved.connect(self._steps_reordered)
        self.step_list.itemDoubleClicked.connect(lambda _: self.edit_step())
        self.step_list.currentRowChanged.connect(lambda _: self._update_buttons())
        self.add_btn = QToolButton(text="Add step")
        self.add_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        add_menu = QMenu(self)
        for op in OPS:
            add_menu.addAction(OP_LABELS[op], lambda o=op: self.add_step(o))
        self.add_btn.setMenu(add_menu)
        self.edit_btn = QPushButton("Edit…")
        self.edit_btn.clicked.connect(self.edit_step)
        self.remove_btn = QPushButton("Remove")
        self.remove_btn.clicked.connect(self.remove_step)
        self.up_btn = QPushButton("▲")
        self.up_btn.setToolTip("Move up (or drag)")
        self.up_btn.clicked.connect(lambda: self.move_step(-1))
        self.down_btn = QPushButton("▼")
        self.down_btn.setToolTip("Move down (or drag)")
        self.down_btn.clicked.connect(lambda: self.move_step(1))
        step_buttons = QHBoxLayout()
        for w in (self.add_btn, self.edit_btn, self.remove_btn, self.up_btn, self.down_btn):
            step_buttons.addWidget(w)
        step_buttons.addStretch(1)

        self.record_btn = QPushButton("● Record")
        self.record_btn.setCheckable(True)
        self.record_btn.setToolTip("Append your reads, writes and subscriptions (GATT and UART tabs) "
                                   "to the selected macro")
        self.record_btn.toggled.connect(self.set_recording)
        self.run_btn = QPushButton("▶ Run")
        self.run_btn.clicked.connect(self.run_selected)
        self.stop_btn = QPushButton("■ Stop")
        self.stop_btn.clicked.connect(self.runner.stop)
        self.repeat = QSpinBox(minimum=1, maximum=100_000, prefix="× ")
        self.repeat.setToolTip("Number of runs")
        self.stop_on_error = QCheckBox("Stop on error")
        self.stop_on_error.setChecked(True)
        self.status = QLabel()
        run_bar = QHBoxLayout()
        for w in (self.record_btn, self.run_btn, self.stop_btn, self.repeat, self.stop_on_error):
            run_bar.addWidget(w)
        run_bar.addStretch(1)
        run_bar.addWidget(self.status)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(QLabel("<b>Steps</b> <span style='color:gray'>(double-click to edit,"
                                      " drag to reorder)</span>"))
        right_layout.addWidget(self.step_list, 1)
        right_layout.addLayout(step_buttons)

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(1, 3)
        split.setSizes([220, 660])
        layout = QVBoxLayout(self)
        layout.addWidget(split, 1)
        layout.addLayout(run_bar)

        self.runner.step_started.connect(self._on_step_started)
        self.runner.step_finished.connect(self._on_step_finished)
        self.runner.running_changed.connect(lambda _: self._update_buttons())
        self.runner.finished.connect(self._on_finished)
        session.operation.connect(self._on_operation)
        session.connection_changed.connect(lambda _: self._update_buttons())
        library.changed.connect(self._reload)
        if library.load_error:
            self.status.setText(library.load_error)
        self._reload()

    # -- library ----------------------------------------------------------
    @property
    def macro(self) -> Macro | None:
        row = self.macro_list.currentRow()
        return self.library.macros[row] if 0 <= row < len(self.library.macros) else None

    def _reload(self) -> None:
        current = self.macro
        row = self.macro_list.currentRow()
        self.macro_list.blockSignals(True)
        self.macro_list.clear()
        for m in self.library.macros:
            item = QListWidgetItem(m.name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
            self.macro_list.addItem(item)
        if current in self.library.macros:
            row = self.library.macros.index(current)
        self.macro_list.setCurrentRow(min(max(row, 0), len(self.library.macros) - 1))
        self.macro_list.blockSignals(False)
        self._show_steps()

    def _save(self) -> None:
        try:
            self.library.save()
        except OSError as exc:
            self.status.setText(f"Could not save macros: {exc}")

    def select(self, macro: Macro) -> None:
        self.macro_list.setCurrentRow(self.library.macros.index(macro))

    def new_macro(self, name: str | None = None) -> Macro:
        m = Macro(self.library.unique_name(name or "New macro"))
        self.library.macros.append(m)
        self._save()
        self.select(m)
        return m

    def duplicate_macro(self) -> None:
        if self.macro is None:
            return
        m = Macro(self.library.unique_name(self.macro.name), copy.deepcopy(self.macro.steps))
        self.library.macros.insert(self.macro_list.currentRow() + 1, m)
        self._save()
        self.select(m)

    def _start_rename(self) -> None:
        item = self.macro_list.currentItem()
        if item is not None:
            self.macro_list.editItem(item)

    def _renamed(self, item: QListWidgetItem) -> None:
        row = self.macro_list.row(item)
        name = item.text().strip()
        if 0 <= row < len(self.library.macros) and name and name != self.library.macros[row].name:
            self.library.macros[row].name = name
            self._save()

    def delete_macro(self, confirm: bool = True) -> None:
        m = self.macro
        if m is None:
            return
        if confirm and m.steps and QMessageBox.question(
                self, "Delete macro", f"Delete '{m.name}' ({len(m.steps)} steps)?") \
                != QMessageBox.StandardButton.Yes:
            return
        self.library.macros.remove(m)
        self._save()

    def _import_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import macros", "", "Macro files (*.json);;All files (*)")
        if path:
            self.import_file(Path(path))

    def import_file(self, path: Path) -> int:
        try:
            imported = loads(path.read_text("utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            QMessageBox.warning(self, "Import failed", f"{path.name}: {exc}")
            return 0
        for m in imported:
            m.name = self.library.unique_name(m.name)
            self.library.macros.append(m)
        self._save()
        if imported:
            self.select(imported[0])
        self.session.log.emit("info", f"Imported {len(imported)} macro(s) from {path}")
        return len(imported)

    def _export_dialog(self) -> None:
        if self.macro is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export macro", f"{self.macro.name}.json",
                                              "Macro files (*.json)")
        if path:
            self.export_file(Path(path))

    def export_file(self, path: Path) -> None:
        try:
            path.write_text(dumps([self.macro]), "utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "Export failed", str(exc))
            return
        self.session.log.emit("info", f"Exported macro '{self.macro.name}' to {path}")

    # -- steps ------------------------------------------------------------
    def _characteristics(self) -> list[tuple[str, str]]:
        out = []
        for svc in self.session.services:
            for ch in svc.characteristics:
                u = uuids.normalize(ch.uuid)
                label = f"{uuids.display_name(ch.uuid, 'characteristics')}  ·  {uuids.short_form(ch.uuid)}"
                if all(u != x for x, _ in out):
                    out.append((u, label))
        return out

    def _show_steps(self) -> None:
        m = self.macro
        row = self.step_list.currentRow()
        self.step_list.clear()
        if m is not None:
            for i, step in enumerate(m.steps):
                item = QListWidgetItem(f"{i + 1}. {step.describe()}")
                status = self._step_status.get(i)
                if status is not None:
                    item.setForeground(QBrush(OK_COLOR if status else FAIL_COLOR))
                    item.setText(("✓ " if status else "✗ ") + item.text())
                if i == self._current_step:
                    f = QFont()
                    f.setBold(True)
                    item.setFont(f)
                    item.setText("▶ " + item.text())
                self.step_list.addItem(item)
            if m.steps:
                self.step_list.setCurrentRow(min(max(row, 0), len(m.steps) - 1))
        self._update_buttons()

    def add_step(self, op: str, step: Step | None = None, edit: bool = True) -> None:
        m = self.macro or self.new_macro()
        step = step or Step(op)
        if edit:
            dlg = StepDialog(step, self._characteristics(), self)
            if not dlg.exec():
                return
            step = dlg.step
        at = self.step_list.currentRow() + 1 if self.step_list.currentRow() >= 0 else len(m.steps)
        m.steps.insert(at, step)
        self._save()
        self.step_list.setCurrentRow(at)

    def edit_step(self) -> None:
        m, row = self.macro, self.step_list.currentRow()
        if m is None or not 0 <= row < len(m.steps) or self.runner.running:
            return
        dlg = StepDialog(m.steps[row], self._characteristics(), self)
        if dlg.exec():
            m.steps[row] = dlg.step
            self._save()

    def remove_step(self) -> None:
        m, row = self.macro, self.step_list.currentRow()
        if m is not None and 0 <= row < len(m.steps):
            del m.steps[row]
            self._save()

    def move_step(self, delta: int) -> None:
        m, row = self.macro, self.step_list.currentRow()
        if m is None or not 0 <= row < len(m.steps) or not 0 <= row + delta < len(m.steps):
            return
        m.steps[row], m.steps[row + delta] = m.steps[row + delta], m.steps[row]
        self._save()
        self.step_list.setCurrentRow(row + delta)

    def _steps_reordered(self, *_args) -> None:
        # Drag & drop moved list items; rebuild the order from their numbers.
        m = self.macro
        if m is None:
            return
        order = [int(self.step_list.item(i).text().lstrip("▶✓✗ ").split(".", 1)[0]) - 1
                 for i in range(self.step_list.count())]
        m.steps = [m.steps[i] for i in order]
        self._save()

    # -- recording ----------------------------------------------------------
    def set_recording(self, on: bool) -> None:
        if on and self.macro is None:
            self.new_macro(f"Recorded {time.strftime('%H:%M')}")
        self.recording = on
        self.record_btn.setChecked(on)
        self.record_btn.setText("■ Stop recording" if on else "● Record")
        self.status.setText(f"Recording into '{self.macro.name}'…" if on else "")
        self._update_buttons()

    def _on_operation(self, op: str, handle: int, data: bytes, response: bool) -> None:
        if not self.recording or self.runner.running or self.macro is None:
            return
        ch = self.session.characteristic(handle)
        if ch is None:
            return
        u = uuids.normalize(ch.uuid)
        step = Step(op, uuid=u, data=to_hex(data, " ") if op == "write" else "",
                    response=response)
        self.macro.steps.append(step)
        self._save()
        self.step_list.setCurrentRow(len(self.macro.steps) - 1)

    # -- running ------------------------------------------------------------
    def run_selected(self) -> None:
        if self.macro is None or not self.macro.steps or self.runner.running:
            return
        if self.recording:
            self.set_recording(False)
        self._step_status.clear()
        self.status.setText("Running…")
        self.runner.start(copy.deepcopy(self.macro), self.repeat.value(),
                          self.stop_on_error.isChecked())

    def _on_step_started(self, i: int) -> None:
        self._current_step = i
        if i == 0:
            self._step_status.clear()
        self._show_steps()

    def _on_step_finished(self, i: int, ok: bool) -> None:
        self._step_status[i] = ok
        self._current_step = None
        self._show_steps()

    def _on_finished(self, ok: bool, summary: str) -> None:
        color = OK_COLOR if ok else FAIL_COLOR
        self.status.setText(f"<span style='color:{color.name()}'>{summary.capitalize()}</span>")

    def _update_buttons(self) -> None:
        has_macro = self.macro is not None
        has_step = has_macro and 0 <= self.step_list.currentRow() < len(self.macro.steps)
        running = self.runner.running
        editable = not running
        for w in (self.edit_btn, self.remove_btn, self.up_btn, self.down_btn):
            w.setEnabled(editable and has_step)
        self.add_btn.setEnabled(editable)
        self.macro_list.setEnabled(not running and not self.recording)
        for a in (self.dup_action, self.rename_action, self.delete_action, self.export_action):
            a.setEnabled(has_macro and editable)
        self.run_btn.setEnabled(has_macro and bool(self.macro.steps) and not running
                                and self.session.is_connected)
        self.stop_btn.setEnabled(running)
        self.record_btn.setEnabled(not running)
