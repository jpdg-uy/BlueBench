"""Dialog for writing a value to a characteristic or descriptor."""
from __future__ import annotations

from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel,
                               QRadioButton, QHBoxLayout, QWidget)

from ..ble.formats import ValueFormat, encode, to_hex

HISTORY_SIZE = 15
_history: list[tuple[str, ValueFormat]] = []  # shared across dialogs, newest first


class WriteDialog(QDialog):
    def __init__(self, title: str, *, can_request: bool, can_command: bool,
                 default_format: ValueFormat = ValueFormat.HEX, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"Write · {title}")
        self.setMinimumWidth(460)
        self.data: bytes = b""
        self.with_response = can_request

        self.format_combo = QComboBox()
        for f in ValueFormat:
            self.format_combo.addItem(f.value, f)
        self.format_combo.setCurrentIndex(list(ValueFormat).index(default_format))

        self.value_edit = QComboBox(editable=True)
        self.value_edit.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for text, fmt in _history:
            self.value_edit.addItem(f"{text}", (text, fmt))
        self.value_edit.setCurrentText("")
        self.value_edit.lineEdit().setPlaceholderText("e.g. 01-02-FF, hello, 1234")
        self.value_edit.activated.connect(self._history_picked)

        self.preview = QLabel()
        self.preview.setWordWrap(True)

        self.req_radio = QRadioButton("Request (with response)")
        self.cmd_radio = QRadioButton("Command (without response)")
        self.req_radio.setEnabled(can_request)
        self.cmd_radio.setEnabled(can_command)
        (self.req_radio if can_request else self.cmd_radio).setChecked(True)
        write_type = QHBoxLayout()
        write_type.addWidget(self.req_radio)
        write_type.addWidget(self.cmd_radio)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                        | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Write")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        form = QFormLayout(self)
        form.addRow("Format", self.format_combo)
        form.addRow("Value", self.value_edit)
        form.addRow("Bytes", self.preview)
        form.addRow("Write type", write_type)
        form.addRow(self.buttons)

        self.format_combo.currentIndexChanged.connect(self._update_preview)
        self.value_edit.editTextChanged.connect(self._update_preview)
        self._update_preview()

    @property
    def fmt(self) -> ValueFormat:
        return ValueFormat(self.format_combo.currentData())

    def _history_picked(self, index: int) -> None:
        item = self.value_edit.itemData(index)
        if item:
            text, fmt = item[0], ValueFormat(item[1])
            self.format_combo.setCurrentIndex(list(ValueFormat).index(fmt))
            self.value_edit.setCurrentText(text)

    def _update_preview(self, *_) -> None:
        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        try:
            self.data = encode(self.value_edit.currentText(), self.fmt)
        except ValueError as exc:
            self.preview.setText(f"<span style='color:#c62828'>{exc}</span>")
            ok.setEnabled(False)
            return
        self.preview.setText(f"{len(self.data)} byte(s): 0x{to_hex(self.data)}"
                             if self.data else "(empty value)")
        ok.setEnabled(True)

    def accept(self) -> None:
        self.with_response = self.req_radio.isChecked()
        entry = (self.value_edit.currentText(), self.fmt)
        if entry in _history:
            _history.remove(entry)
        _history.insert(0, entry)
        del _history[HISTORY_SIZE:]
        super().accept()
