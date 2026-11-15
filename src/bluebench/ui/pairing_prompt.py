"""Dialogs answering a peripheral's pairing request (passkey, numeric comparison).

The dialogs are opened non-modally and awaited through their ``finished``
signal: a nested ``exec()`` loop inside a running qasync task is not safe.
"""
from __future__ import annotations

import asyncio

from PySide6.QtWidgets import QDialog, QInputDialog, QLineEdit, QMessageBox, QWidget


async def run_dialog(dialog: QDialog) -> int:
    fut: asyncio.Future[int] = asyncio.get_running_loop().create_future()
    dialog.finished.connect(lambda r: fut.done() or fut.set_result(r))
    dialog.open()
    return await fut


class PairingPrompt:
    """Callable passed to DeviceSession.pair(); see ble.pairing.Prompt."""

    def __init__(self, parent: QWidget, device_name: str) -> None:
        self.parent = parent
        self.device_name = device_name
        self.dialogs: list[QDialog] = []

    async def __call__(self, kind: str, pin: str) -> str | bool | None:
        if kind == "confirm":
            return True  # Just Works: the user already asked to pair
        if kind == "display_pin":
            box = self._box(QMessageBox.Icon.Information,
                            f"Type this passkey on <b>{self.device_name}</b>:"
                            f"<h1 style='letter-spacing:4px'>{pin}</h1>",
                            QMessageBox.StandardButton.Close)
            box.show()  # stays up until pairing finishes (see close())
            return True
        if kind == "confirm_pin":
            box = self._box(QMessageBox.Icon.Question,
                            f"Does <b>{self.device_name}</b> show this passkey?"
                            f"<h1 style='letter-spacing:4px'>{pin}</h1>",
                            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            await run_dialog(box)
            clicked = box.clickedButton()
            return clicked is not None and \
                box.standardButton(clicked) == QMessageBox.StandardButton.Yes
        if kind == "provide_pin":
            dlg = QInputDialog(self.parent)
            dlg.setWindowTitle("Pairing")
            dlg.setLabelText(f"Enter the passkey shown by {self.device_name}:")
            dlg.setTextEchoMode(QLineEdit.EchoMode.Normal)
            dlg.setInputMode(QInputDialog.InputMode.TextInput)
            self.dialogs.append(dlg)
            if await run_dialog(dlg) != QDialog.DialogCode.Accepted:
                return None
            return dlg.textValue().strip() or None
        return False

    def _box(self, icon, text: str, buttons) -> QMessageBox:
        box = QMessageBox(icon, "Pairing", text, buttons, self.parent)
        self.dialogs.append(box)
        return box

    def close(self) -> None:
        """Dismiss anything still open once pairing has finished."""
        for d in self.dialogs:
            d.close()
            d.deleteLater()
        self.dialogs.clear()
