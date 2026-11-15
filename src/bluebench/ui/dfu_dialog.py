"""Dialog: pick a DFU package, run Secure DFU, show progress."""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSpinBox,
                               QVBoxLayout, QWidget)

from ..ble.connection import DeviceSession
from ..tools.dfu import package as dfu_package
from ..tools.dfu.secure_dfu import SecureDfu

PRN_TIP = ("Packet receipt notification: the bootloader confirms every N packets. 0 (off) is "
           "fastest. Try 10–20 if transfers fail midway.")


class DfuDialog(QDialog):
    def __init__(self, session: DeviceSession, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"DFU · {session.name}")
        self.setMinimumWidth(560)
        self.session = session
        self.package: dfu_package.DfuPackage | None = None
        self.dfu: SecureDfu | None = None
        self._last_progress = (0.0, 0)

        self.path_edit = QLineEdit(readOnly=True, placeholderText="Nordic DFU package (.zip)")
        choose = QPushButton("Choose…")
        choose.clicked.connect(self._choose)
        self.choose_btn = choose
        file_row = QHBoxLayout()
        file_row.addWidget(self.path_edit, 1)
        file_row.addWidget(choose)
        self.contents = QLabel()
        self.contents.setWordWrap(True)
        self.prn = QSpinBox(minimum=0, maximum=200)
        self.prn.setToolTip(PRN_TIP)

        self.stage = QLabel("Choose a package to start.")
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.speed = QLabel()
        self.speed.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.log_view = QPlainTextEdit(readOnly=True)
        self.log_view.setMaximumBlockCount(500)
        self.log_view.setFixedHeight(130)

        self.start_btn = QPushButton("Start")
        self.start_btn.setEnabled(False)
        self.start_btn.clicked.connect(self.start)
        self.abort_btn = QPushButton("Abort")
        self.abort_btn.setEnabled(False)
        self.abort_btn.clicked.connect(self.abort)
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.close)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        for b in (self.start_btn, self.abort_btn, self.close_btn):
            buttons.addWidget(b)

        form = QFormLayout()
        form.addRow("Package", file_row)
        form.addRow("", self.contents)
        form.addRow("PRN", self.prn)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.stage)
        layout.addWidget(self.bar)
        layout.addWidget(self.speed)
        layout.addWidget(self.log_view)
        layout.addLayout(buttons)

    # -- package --------------------------------------------------------------
    def _choose(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "DFU package", "",
                                              "DFU packages (*.zip);;All files (*)")
        if path:
            self.load_package(path)

    def load_package(self, path: str | Path) -> bool:
        self.path_edit.setText(str(path))
        try:
            self.package = dfu_package.load(path)
        except dfu_package.PackageError as exc:
            self.package = None
            self.contents.setText(f"<span style='color:#c62828'>{exc}</span>")
            self.start_btn.setEnabled(False)
            return False
        self.contents.setText(self.package.describe())
        self.start_btn.setEnabled(True)
        self.stage.setText("Ready. The device restarts when the update is applied.")
        return True

    # -- run --------------------------------------------------------------------
    def start(self) -> None:
        if self.package is None or (self.dfu and self.dfu.running):
            return
        self.log_view.clear()
        self.bar.setValue(0)
        self.dfu = SecureDfu(self.session, self.package, prn=self.prn.value(), parent=self)
        self.dfu.stage.connect(self.stage.setText)
        self.dfu.progress.connect(self._on_progress)
        self.dfu.log.connect(self._on_log)
        self.dfu.finished.connect(self._on_finished)
        self._last_progress = (time.monotonic(), 0)
        self._set_running(True)
        self.dfu.start()

    def abort(self) -> None:
        if self.dfu is not None:
            self.dfu.abort()

    def _set_running(self, running: bool) -> None:
        self.start_btn.setEnabled(not running and self.package is not None)
        self.choose_btn.setEnabled(not running)
        self.prn.setEnabled(not running)
        self.abort_btn.setEnabled(running)
        self.close_btn.setEnabled(not running)

    def _on_progress(self, done: int, total: int) -> None:
        self.bar.setValue(int(done * 100 / total) if total else 0)
        elapsed = time.monotonic() - (self.dfu.started_at if self.dfu else time.monotonic())
        if elapsed > 0.5 and done:
            rate = done / elapsed
            eta = (total - done) / rate if rate else 0
            self.speed.setText(f"{done / 1024:.1f} / {total / 1024:.1f} KB · "
                               f"{rate / 1024:.1f} KB/s · {eta:.0f} s left")

    def _on_log(self, level: str, msg: str) -> None:
        # Stages and problems here; raw bootloader traffic only in the main log.
        if level in ("warning", "error") or (level == "info" and not msg.startswith("[bootloader]")):
            self.log_view.appendPlainText(msg)
        self.session.log.emit(level, f"DFU: {msg}")

    def _on_finished(self, ok: bool, summary: str) -> None:
        self._set_running(False)
        self.stage.setText(summary)
        if ok:
            self.bar.setValue(100)
            self.speed.setText(self.speed.text().split(" · ")[0] if self.speed.text() else "")

    def closeEvent(self, event) -> None:
        if self.dfu is not None and self.dfu.running:
            if QMessageBox.question(self, "DFU running", "Abort the update?") \
                    != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.dfu.abort()
        super().closeEvent(event)
