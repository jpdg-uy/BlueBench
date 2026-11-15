"""Main window: a Scanner tab plus one tab per connected device."""
from __future__ import annotations

import asyncio
from importlib import resources

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QDockWidget, QMainWindow, QTabWidget

from .. import __version__
from ..ble.connection import DeviceSession
from ..ble.scanner import Scanner
from ..core.log_bus import LogBus
from ..sniffer.app_capture import AppCapture
from ..sniffer.model import PacketStore
from ..sniffer.nrf_sniffer import AirCapture
from .device_view import DeviceView
from .log_view import LogView
from .scanner_view import ScannerView
from .traffic_view import TrafficView


SHUTDOWN_TIMEOUT_S = 10.0


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"BlueBench {__version__}")
        icon = resources.files("bluebench.data").joinpath("icon.png")
        if icon.is_file():
            self.setWindowIcon(QIcon(str(icon)))
        self.resize(1200, 800)

        self.log_bus = LogBus(parent=self)
        self.log_bus.entry_added.connect(self._on_log_entry)

        self.scanner = Scanner(self)
        self.scanner.scanning_changed.connect(
            lambda on: self.log_bus.log("info", "Scanner", "Scanning started" if on else "Scanning stopped"))
        self.scanner.error.connect(lambda msg: self.log_bus.log("error", "Scanner", msg))
        self.scanner_view = ScannerView(self.scanner)
        self.scanner_view.connect_requested.connect(self.open_device)

        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.addTab(self.scanner_view, "Scanner")
        self.tabs.tabBar().setTabButton(0, self.tabs.tabBar().ButtonPosition.RightSide, None)

        self.packets = PacketStore(self)
        self.app_capture = AppCapture(self.packets, self)
        self.air_capture = AirCapture(self.packets, self)
        self.air_capture.message.connect(lambda level, msg: self.log_bus.log(level, "Sniffer", msg))
        self.traffic_view = TrafficView(self.packets, self.app_capture, self.air_capture)
        self.tabs.addTab(self.traffic_view, "Traffic")
        self.tabs.tabBar().setTabButton(1, self.tabs.tabBar().ButtonPosition.RightSide, None)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.setCentralWidget(self.tabs)

        self.log_view = LogView(self.log_bus)
        self.log_dock = QDockWidget("Log", self)
        self.log_dock.setObjectName("log_dock")
        self.log_dock.setWidget(self.log_view)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.log_dock)
        self.resizeDocks([self.log_dock], [240], Qt.Orientation.Vertical)
        view_menu = self.menuBar().addMenu("&View")
        view_menu.addAction(self.log_dock.toggleViewAction())

        self.views: dict[str, DeviceView] = {}  # address -> tab
        self._closing = False
        self._ready_to_close = False
        self.show_status("Ready. Press 'Start scan' to discover devices.")

    def show_status(self, msg: str) -> None:
        self.statusBar().showMessage(msg, 6000)

    # -- device tabs -----------------------------------------------------
    def open_device(self, address: str) -> None:
        view = self.views.get(address)
        if view is None:
            entry = self.scanner.devices.get(address)
            device = entry.device if entry else address
            session = DeviceSession(device, entry.name if entry else None, self)
            source = f"{session.name} · {session.address}" if session.name != "N/A" else session.address
            session.log.connect(lambda level, msg, src=source: self.log_bus.log(level, src, msg))
            # Pairing in a device tab updates the Bond column right away.
            session.bond_changed.connect(lambda _: self.scanner_view.request_bond_refresh())
            self.app_capture.attach_session(session)
            view = DeviceView(session)
            view.title_changed.connect(lambda title, v=view: self._set_tab_title(v, title))
            self.views[address] = view
            self.tabs.addTab(view, session.name)
            self._set_tab_title(view, f"◌ {session.name}")
        self.tabs.setCurrentWidget(view)
        if view.session.state == "disconnected":
            asyncio.ensure_future(view.session.connect())

    def _set_tab_title(self, view: DeviceView, title: str) -> None:
        idx = self.tabs.indexOf(view)
        if idx >= 0:
            self.tabs.setTabText(idx, title)
            self.tabs.setTabToolTip(idx, view.session.address)

    def _close_tab(self, index: int) -> None:
        view = self.tabs.widget(index)
        if not isinstance(view, DeviceView):
            return
        self.tabs.removeTab(index)
        self.views.pop(view.session.address, None)

        async def cleanup() -> None:
            await view.session.disconnect()
            view.deleteLater()
            view.session.deleteLater()
        asyncio.ensure_future(cleanup())

    def _on_log_entry(self, entry) -> None:
        # Warnings and errors also flash in the status bar, so they are noticed
        # even with the log dock hidden.
        if entry.level in ("warning", "error"):
            self.show_status(f"{entry.source}: {entry.message}")

    # -- closing ---------------------------------------------------------------
    def closeEvent(self, event) -> None:
        # Disconnecting is async, so the first close is deferred: hide the window,
        # shut down cleanly, then quit the application.
        if self._ready_to_close:
            event.accept()
            return
        event.ignore()
        if not self._closing:
            self._closing = True
            self.hide()
            asyncio.ensure_future(self._shutdown_then_quit())

    async def _shutdown_then_quit(self) -> None:
        try:
            await asyncio.wait_for(self.shutdown(), timeout=SHUTDOWN_TIMEOUT_S)
        except Exception as exc:  # a stuck device must not keep the app alive
            self.log_bus.log("warning", "App", f"Shutdown incomplete: {exc!r}")
        finally:
            self._ready_to_close = True
            self.close()
            QApplication.instance().quit()

    async def shutdown(self) -> None:
        self.air_capture.stop()
        await self.scanner.stop()
        await asyncio.gather(*(v.session.disconnect() for v in self.views.values()),
                             return_exceptions=True)
