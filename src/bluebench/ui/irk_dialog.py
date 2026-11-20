"""Tools → Identity keys: the IRKs the scanner resolves private addresses with."""
from __future__ import annotations

from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ..ble import irk

HELP = ("With a device's Identity Resolving Key (IRK), BlueBench recognises every private "
        "address (RPA) it advertises with: its row keeps following it, with no guessing, "
        "and <code>irk:LABEL</code> filters for it.<br>Write the 16 bytes in hex; either byte "
        "order works. Keys are kept in <code>~/.bluebench/irks.json</code>.")


class IrkDialog(QDialog):
    def __init__(self, keys: irk.KeyRing, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Identity keys (IRK)")
        self.resize(640, 380)
        self.keys = keys

        info = QLabel(HELP)
        info.setWordWrap(True)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Label", "IRK"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().hide()

        self.label_edit = QLineEdit(placeholderText="Label, e.g. Board 3")
        self.irk_edit = QLineEdit(placeholderText="IRK: 32 hex digits")
        self.irk_edit.returnPressed.connect(self.add)
        add = QPushButton("Add")
        add.clicked.connect(self.add)
        self.remove_btn = QPushButton("Remove")
        self.remove_btn.clicked.connect(self.remove_selected)
        row = QHBoxLayout()
        row.addWidget(self.label_edit, 1)
        row.addWidget(self.irk_edit, 3)
        row.addWidget(add)
        row.addWidget(self.remove_btn)

        self.check_edit = QLineEdit(placeholderText="Check an address, e.g. 70:81:94:0D:FB:AA")
        self.check_edit.textChanged.connect(self.check)
        self.check_result = QLabel()
        self.status = QLabel()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(info)
        layout.addWidget(self.table, 1)
        layout.addLayout(row)
        layout.addWidget(self.status)
        check = QHBoxLayout()
        check.addWidget(self.check_edit, 1)
        check.addWidget(self.check_result, 1)
        layout.addLayout(check)
        layout.addWidget(buttons)
        self._fill()

    def _fill(self) -> None:
        self.table.setRowCount(0)
        for k in self.keys.keys:
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(k.label))
            self.table.setItem(r, 1, QTableWidgetItem(k.irk.hex(" ").upper()))
        self.remove_btn.setEnabled(bool(self.keys.keys))
        self.check(self.check_edit.text())

    def add(self) -> None:
        try:
            self.keys.add(self.label_edit.text(), self.irk_edit.text())
            self.keys.save()
        except (ValueError, OSError) as exc:
            self.status.setText(f"⚠ {exc}")
            return
        self.status.setText(f"Added “{self.label_edit.text().strip()}”. It applies to new packets.")
        self.label_edit.clear()
        self.irk_edit.clear()
        self._fill()

    def remove_selected(self) -> None:
        labels = {self.table.item(i.row(), 0).text() for i in self.table.selectedIndexes()}
        for label in labels:
            self.keys.remove(label)
        if labels:
            try:
                self.keys.save()
            except OSError as exc:
                self.status.setText(f"⚠ {exc}")
            self._fill()

    def check(self, text: str) -> None:
        text = text.strip()
        if not text:
            self.check_result.setText("")
        elif irk.address_bytes(text) is None:
            self.check_result.setText("not an address")
        elif not irk.is_rpa(text):
            self.check_result.setText("not a resolvable private address")
        else:
            k = self.keys.resolve(text)
            self.check_result.setText(f"✓ {k.label}" if k else "no key resolves it")
