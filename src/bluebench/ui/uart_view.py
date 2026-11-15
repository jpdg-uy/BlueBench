"""UART terminal for devices exposing the Nordic UART Service."""
from __future__ import annotations

import asyncio
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QKeyEvent, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)

from ..ble.formats import ValueFormat, encode, to_hex
from ..tools.nus import NusClient

LINE_ENDINGS = {"None": b"", "LF (\\n)": b"\n", "CR (\\r)": b"\r", "CR+LF": b"\r\n"}
MAX_BLOCKS = 5000
RX_COLOR = QColor("#1565c0")
TX_COLOR = QColor("#2e7d32")


class HistoryLineEdit(QLineEdit):
    """Line edit with shell-style Up/Down history."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.history: list[str] = []
        self._pos = 0

    def remember(self, text: str) -> None:
        if text and (not self.history or self.history[-1] != text):
            self.history.append(text)
        self._pos = len(self.history)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Up and self.history:
            self._pos = max(0, self._pos - 1)
            self.setText(self.history[self._pos])
        elif event.key() == Qt.Key.Key_Down and self.history:
            self._pos = min(len(self.history), self._pos + 1)
            self.setText(self.history[self._pos] if self._pos < len(self.history) else "")
        else:
            super().keyPressEvent(event)


class UartView(QWidget):
    def __init__(self, nus: NusClient, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.nus = nus

        self.enable_btn = QPushButton("Enable")
        self.enable_btn.setCheckable(True)
        self.enable_btn.setToolTip("Subscribe to NUS TX to receive data")
        self.enable_btn.clicked.connect(self._toggle_enable)
        self.display_combo = QComboBox()
        self.display_combo.addItems(["Text", "Hex"])
        self.display_combo.setToolTip("How received data is shown")
        self.timestamps = QCheckBox("Timestamps")
        self.echo = QCheckBox("Echo sent")
        self.echo.setChecked(True)
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self.clear)
        self.counters = QLabel()
        top = QHBoxLayout()
        top.addWidget(self.enable_btn)
        top.addWidget(QLabel("Show as"))
        top.addWidget(self.display_combo)
        top.addWidget(self.timestamps)
        top.addWidget(self.echo)
        top.addStretch(1)
        top.addWidget(self.counters)
        top.addWidget(clear_btn)

        self.output = QPlainTextEdit(readOnly=True)
        self.output.setMaximumBlockCount(MAX_BLOCKS)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.output.setFont(mono)

        self.input = HistoryLineEdit(placeholderText="Type and press Enter to send")
        self.input.setFont(mono)
        self.input.returnPressed.connect(self._send)
        self.send_format = QComboBox()
        self.send_format.addItems(["Text", "Hex"])
        self.ending_combo = QComboBox()
        self.ending_combo.addItems(list(LINE_ENDINGS))
        self.ending_combo.setCurrentText("LF (\\n)")
        self.ending_combo.setToolTip("Appended to text sends")
        self.send_btn = QPushButton("Send")
        self.send_btn.clicked.connect(self._send)
        bottom = QHBoxLayout()
        bottom.addWidget(self.input, 1)
        bottom.addWidget(self.send_format)
        bottom.addWidget(self.ending_combo)
        bottom.addWidget(self.send_btn)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.output, 1)
        layout.addLayout(bottom)

        self._at_line_start = True
        nus.received.connect(self._on_received)
        nus.sent.connect(self._on_sent)
        nus.active_changed.connect(self._on_active)
        nus.error.connect(lambda msg: self._append_note(msg))
        nus.session.connection_changed.connect(lambda _: self._update_enabled())
        self._on_active(nus.active)

    # -- state -------------------------------------------------------------
    def _toggle_enable(self, checked: bool) -> None:
        self.enable_btn.setEnabled(False)

        async def run() -> None:
            await (self.nus.start() if checked else self.nus.stop())
            self._on_active(self.nus.active)
        asyncio.ensure_future(run())

    def _on_active(self, active: bool) -> None:
        self.enable_btn.setChecked(active)
        self.enable_btn.setText("Disable" if active else "Enable")
        self._update_enabled()

    def _update_enabled(self) -> None:
        connected = self.nus.session.is_connected
        self.enable_btn.setEnabled(connected)
        for w in (self.input, self.send_btn):
            w.setEnabled(connected)
        self._update_counters()

    def _update_counters(self) -> None:
        self.counters.setText(f"RX {self.nus.rx_bytes} B · TX {self.nus.tx_bytes} B")

    def clear(self) -> None:
        self.output.clear()
        self._at_line_start = True

    # -- sending -------------------------------------------------------------
    def build_payload(self) -> bytes:
        """Bytes for the current input. Raises ValueError for bad hex."""
        text = self.input.text()
        if self.send_format.currentText() == "Hex":
            return encode(text, ValueFormat.HEX)
        return text.encode("utf-8") + LINE_ENDINGS[self.ending_combo.currentText()]

    def _send(self) -> None:
        if not self.send_btn.isEnabled():
            return
        try:
            payload = self.build_payload()
        except ValueError as exc:
            self._append_note(str(exc))
            return
        if not payload:
            return
        self.input.remember(self.input.text())
        self.input.clear()

        async def run() -> None:
            # Sending without an active subscription is allowed (TX-only devices),
            # but replies would be missed, so subscribe first when possible.
            if not self.nus.active:
                await self.nus.start()
            await self.nus.send(payload)
            self._update_counters()
        asyncio.ensure_future(run())

    # -- output --------------------------------------------------------------
    def _on_received(self, data: bytes) -> None:
        if self.display_combo.currentText() == "Hex":
            self._append_line(to_hex(data, " "), RX_COLOR, prefix="← ")
        else:
            self._append_stream(data.decode("utf-8", errors="replace"), RX_COLOR)
        self._update_counters()

    def _on_sent(self, data: bytes) -> None:
        if self.echo.isChecked():
            if self.display_combo.currentText() == "Hex":
                shown = to_hex(data, " ")
            else:
                shown = data.decode("utf-8", errors="replace").rstrip("\r\n")
            self._append_line(shown, TX_COLOR, prefix="→ ")
        self._update_counters()

    def _stamp(self) -> str:
        return time.strftime("[%H:%M:%S] ") if self.timestamps.isChecked() else ""

    def _cursor_at_end(self) -> QTextCursor:
        cur = self.output.textCursor()
        cur.movePosition(QTextCursor.MoveOperation.End)
        return cur

    def _append_stream(self, text: str, color: QColor) -> None:
        """Terminal-style: received text flows on, breaking only at newlines."""
        fmt = QTextCharFormat()
        fmt.setForeground(color)
        cur = self._cursor_at_end()
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        for i, line in enumerate(text.split("\n")):
            if i > 0:
                cur.insertText("\n", fmt)
                self._at_line_start = True
            if line:
                if self._at_line_start:
                    cur.insertText(self._stamp(), fmt)
                cur.insertText(line, fmt)
                self._at_line_start = False
        self._scroll()

    def _append_line(self, text: str, color: QColor, prefix: str = "") -> None:
        fmt = QTextCharFormat()
        fmt.setForeground(color)
        cur = self._cursor_at_end()
        if not self._at_line_start:
            cur.insertText("\n", fmt)
        cur.insertText(f"{self._stamp()}{prefix}{text}\n", fmt)
        self._at_line_start = True
        self._scroll()

    def _append_note(self, text: str) -> None:
        self._append_line(text, QColor("#c62828"), prefix="! ")

    def _scroll(self) -> None:
        sb = self.output.verticalScrollBar()
        sb.setValue(sb.maximum())

    def text(self) -> str:
        return self.output.toPlainText()
