"""The BLE bonds Windows keeps, to remove stale ones.

During development bonds pile up: a board that is erased or reflashed forgets its
keys while Windows keeps them, and the next encrypted connection fails.
"""
from __future__ import annotations

import asyncio

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..ble import pairing


class BondsDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Bonded devices")
        self.resize(520, 340)
        self.list = QTreeWidget()
        self.list.setHeaderLabels(["Name", "Identity address"])
        self.list.setColumnWidth(0, 260)
        self.list.setRootIsDecorated(False)
        self.list.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.list.itemSelectionChanged.connect(self._update_buttons)
        self.status = QLabel()
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh)
        self.remove_btn = QPushButton("Remove selected")
        self.remove_btn.clicked.connect(lambda: self._remove(self._selected()))
        self.remove_all_btn = QPushButton("Remove all")
        self.remove_all_btn.clicked.connect(lambda: self._remove(self.bonds, confirm=True))
        close = QPushButton("Close")
        close.clicked.connect(self.accept)

        buttons = QHBoxLayout()
        for b in (self.refresh_btn, self.remove_btn, self.remove_all_btn):
            buttons.addWidget(b)
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Bluetooth LE devices this PC has a bond (pairing keys) with. "
                                "Remove the bond of a device that lost its keys, then pair "
                                "again."))
        layout.addWidget(self.list, 1)
        layout.addWidget(self.status)
        layout.addLayout(buttons)
        self.bonds: list[pairing.BondInfo] = []
        self._busy = False
        self._update_buttons()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh()

    def refresh(self) -> None:
        asyncio.ensure_future(self._refresh())

    async def _refresh(self) -> None:
        bonds = await pairing.bonded_devices()
        self.list.clear()
        self.bonds = sorted(bonds or [], key=lambda b: (b.name.lower(), b.address))
        for b in self.bonds:
            item = QTreeWidgetItem([b.name or "(no name)", b.address])
            item.setData(0, Qt.ItemDataRole.UserRole, b)
            self.list.addTopLevelItem(item)
        if bonds is None:
            self.status.setText("This platform does not report bonds.")
        else:
            self.status.setText(f"{len(self.bonds)} bonded device(s).")
        self._update_buttons()

    def _selected(self) -> list[pairing.BondInfo]:
        return [i.data(0, Qt.ItemDataRole.UserRole) for i in self.list.selectedItems()]

    def _update_buttons(self) -> None:
        self.remove_btn.setEnabled(not self._busy and bool(self.list.selectedItems()))
        self.remove_all_btn.setEnabled(not self._busy and bool(self.bonds))
        self.refresh_btn.setEnabled(not self._busy)

    def _remove(self, bonds: list[pairing.BondInfo], confirm: bool = False) -> None:
        if not bonds:
            return
        if confirm and QMessageBox.question(
                self, "Remove bonds", f"Remove all {len(bonds)} bonds? Every device will "
                "need to pair again.") != QMessageBox.StandardButton.Yes:
            return
        asyncio.ensure_future(self._remove_async(list(bonds)))

    async def _remove_async(self, bonds: list[pairing.BondInfo]) -> None:
        self._busy = True
        self._update_buttons()
        failed = []
        for b in bonds:
            self.status.setText(f"Removing {b.name or b.address}…")
            try:
                await pairing.unpair_bond(b.id)
            except pairing.PairingError as exc:
                failed.append(f"{b.name or b.address}: {exc}")
        self._busy = False
        await self._refresh()
        if failed:
            self.status.setText("Could not remove " + "; ".join(failed))
