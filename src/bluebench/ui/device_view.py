"""Per-device tab: connection header, GATT tree and attribute actions."""
from __future__ import annotations

import asyncio
import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QActionGroup, QBrush, QColor, QFont
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QHeaderView, QLabel, QMenu,
                               QPushButton, QTabWidget, QToolButton, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from ..ble import link, uuids
from ..ble.connection import DeviceSession, is_cccd
from ..ble.formats import ValueFormat, decode
from ..tools.dfu.secure_dfu import supports_dfu
from ..tools.nus import NusClient, has_nus
from .dfu_dialog import DfuDialog
from ..core import macros
from .macro_view import MacroView
from .pairing_prompt import PairingPrompt
from .uart_view import UartView
from .value_dialog import WriteDialog

KIND_ROLE = Qt.ItemDataRole.UserRole
HANDLE_ROLE = Qt.ItemDataRole.UserRole + 1

COL_NAME, COL_UUID, COL_PROPS, COL_VALUE = range(4)

PROP_BADGES = [
    ("read", "R"), ("write", "W"), ("write-without-response", "WNR"),
    ("notify", "N"), ("indicate", "I"), ("broadcast", "B"),
    ("authenticated-signed-writes", "AS"), ("reliable-write", "RW"),
]
_UTF8_CHARS = {uuids.normalize(u) for u in ("2a00", "2a24", "2a25", "2a26", "2a27", "2a28", "2a29")}
_UINT8_CHARS = {uuids.normalize(u) for u in ("2a19", "2a38", "2a06")}

STATE_TEXT = {
    "connecting": ("Connecting…", "#f9a825"),
    "discovering": ("Discovering services…", "#f9a825"),
    "connected": ("Connected", "#2e7d32"),
    "disconnecting": ("Disconnecting…", "#f9a825"),
    "disconnected": ("Disconnected", "#c62828"),
}

MTU_TIP = "ATT MTU negotiated by Windows. Apps cannot request a value."
PHY_TIP = "Windows chooses the PHY (1M / 2M / Coded). Apps cannot select one."
PRIORITY_TIP = ("Ask Windows for a connection-parameter preset. Exact intervals cannot be set "
                "by apps on Windows. Needs Windows 11.")


def default_format(uuid: str) -> ValueFormat:
    u = uuids.normalize(uuid)
    if u in _UTF8_CHARS:
        return ValueFormat.UTF8
    if u in _UINT8_CHARS:
        return ValueFormat.UINT8
    return ValueFormat.HEX


def badges(properties: list[str]) -> str:
    return " ".join(b for p, b in PROP_BADGES if p in properties)


class DeviceView(QWidget):
    title_changed = Signal(str)

    def __init__(self, session: DeviceSession, parent: QWidget | None = None,
                 library: macros.MacroLibrary | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self._items: dict[int, QTreeWidgetItem] = {}
        self._values: dict[int, bytes] = {}
        self._formats: dict[int, ValueFormat] = {}
        self._notify_counts: dict[int, int] = {}

        # header strip
        self.title = QLabel(f"<b>{session.name}</b> &nbsp; {session.address}")
        self.state_label = QLabel()
        self.link_label = QLabel()
        self.bond_label = QLabel()
        self.pair_btn = QPushButton("Pair")
        self.pair_btn.clicked.connect(self._toggle_bond)
        self.link_btn = QToolButton(text="Link")
        self.link_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.link_btn.setToolTip("Connection parameters")
        self.link_btn.setMenu(self._build_link_menu())
        self.dfu_btn = QPushButton("DFU…")
        self.dfu_btn.setToolTip("Firmware update over BLE (Nordic Secure DFU)")
        self.dfu_btn.clicked.connect(self.open_dfu)
        self.dfu_btn.hide()
        self.dfu_dialog: DfuDialog | None = None
        self.connect_btn = QPushButton()
        self.connect_btn.clicked.connect(self._toggle_connection)
        header = QHBoxLayout()
        header.addWidget(self.title)
        header.addStretch(1)
        header.addWidget(self.link_label)
        header.addWidget(self.bond_label)
        header.addWidget(self.state_label)
        header.addWidget(self.link_btn)
        header.addWidget(self.pair_btn)
        header.addWidget(self.dfu_btn)
        header.addWidget(self.connect_btn)

        # GATT tree
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Attribute", "UUID", "Properties", "Value"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        hh = self.tree.header()
        hh.setSectionResizeMode(COL_VALUE, QHeaderView.ResizeMode.Stretch)
        self.tree.setColumnWidth(COL_NAME, 280)
        self.tree.setColumnWidth(COL_UUID, 300)
        self.tree.setColumnWidth(COL_PROPS, 110)
        self.tree.currentItemChanged.connect(lambda *_: self._update_actions())
        self.tree.itemDoubleClicked.connect(lambda *_: self._read_selected())
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)

        # actions
        self.read_btn = QPushButton("Read")
        self.read_btn.clicked.connect(self._read_selected)
        self.write_btn = QPushButton("Write…")
        self.write_btn.clicked.connect(self._write_selected)
        self.notify_btn = QPushButton("Subscribe")
        self.notify_btn.setCheckable(True)
        self.notify_btn.clicked.connect(self._toggle_notify_selected)
        self.format_combo = QComboBox()
        for f in ValueFormat:
            self.format_combo.addItem(f.value, f)
        self.format_combo.setToolTip("How the selected attribute's value is displayed")
        self.format_combo.activated.connect(self._format_changed)
        self.read_all_btn = QPushButton("Read all")
        self.read_all_btn.setToolTip("Read every readable characteristic")
        self.read_all_btn.clicked.connect(lambda: asyncio.ensure_future(self._read_all()))
        actions = QHBoxLayout()
        for w in (self.read_btn, self.write_btn, self.notify_btn):
            actions.addWidget(w)
        actions.addWidget(QLabel("Display as"))
        actions.addWidget(self.format_combo)
        actions.addStretch(1)
        actions.addWidget(self.read_all_btn)

        gatt_page = QWidget()
        gatt_layout = QVBoxLayout(gatt_page)
        gatt_layout.setContentsMargins(0, 0, 0, 0)
        gatt_layout.addWidget(self.tree, 1)
        gatt_layout.addLayout(actions)

        # GATT is always present; tool pages (UART, …) appear when the device supports them.
        self.pages = QTabWidget()
        self.pages.setDocumentMode(True)
        self.pages.addTab(gatt_page, "GATT")
        self.macro_view = MacroView(session, library or macros.library())
        self.pages.addTab(self.macro_view, "Macros")
        self.pages.currentChanged.connect(self._on_page_changed)
        self.nus: NusClient | None = None
        self.uart_view: UartView | None = None
        self._uart_opened = False
        self._resume_nus = False

        layout = QVBoxLayout(self)
        layout.addLayout(header)
        layout.addWidget(self.pages, 1)

        session.state_changed.connect(self._on_state)
        session.services_discovered.connect(self._populate)
        session.value_updated.connect(self._on_value)
        session.notify_changed.connect(self._on_notify_changed)
        session.bond_changed.connect(lambda _: self._update_link_header())
        session.pairing_changed.connect(lambda _: self._update_link_header())
        session.link_changed.connect(self._update_link_header)
        self._on_state(session.state)

    # -- connection ------------------------------------------------------
    def _toggle_connection(self) -> None:
        if self.session.state == "disconnected":
            asyncio.ensure_future(self.session.connect())
        elif self.session.state == "connected":
            asyncio.ensure_future(self.session.disconnect())

    def _on_state(self, state: str) -> None:
        text, color = STATE_TEXT.get(state, (state, "#888"))
        self.state_label.setText(f"<span style='color:{color}'>● {text}</span>")
        busy = state not in ("connected", "disconnected")
        self.connect_btn.setEnabled(not busy)
        self.connect_btn.setText("Disconnect" if state == "connected" else "Connect")
        self._update_link_header()
        connected = state == "connected"
        self.tree.setEnabled(connected)
        self.read_all_btn.setEnabled(connected)
        if state == "disconnected":
            # Emitted before connection_changed, so NUS still reports its old state.
            self._resume_nus = bool(self.nus and self.nus.active)
            self._notify_counts.clear()
            for handle in list(self._items):
                self._refresh_notify_marker(handle, False)
        prefix = {"connected": "●", "disconnected": "○"}.get(state, "◌")
        self.title_changed.emit(f"{prefix} {self.session.name}")
        self._update_actions()

    # -- bonding & link ------------------------------------------------------
    def _build_link_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        self.priority_menu = menu.addMenu("Connection priority")
        self.priority_menu.setToolTipsVisible(True)
        self.priority_group = QActionGroup(self)
        self.priority_actions = {}
        for key, label in link.PRIORITIES.items():
            act = self.priority_menu.addAction(label)
            act.setCheckable(True)
            act.setToolTip(PRIORITY_TIP)
            act.triggered.connect(lambda _=False, k=key: self.session.request_priority(k))
            self.priority_group.addAction(act)
            self.priority_actions[key] = act
        menu.addSeparator()
        self.mtu_action = menu.addAction("Request MTU…")
        self.mtu_action.setEnabled(False)
        self.mtu_action.setToolTip(MTU_TIP)
        self.phy_action = menu.addAction("Set PHY…")
        self.phy_action.setEnabled(False)
        self.phy_action.setToolTip(PHY_TIP)
        menu.addSeparator()
        self.unpair_action = menu.addAction("Remove bond", self._unpair)
        return menu

    def _update_link_header(self) -> None:
        s = self.session
        connected = s.is_connected
        info = s.link if connected else None
        parts = [f"MTU {s.mtu}"] if connected and s.mtu else []
        if info and info.summary():
            parts.append(info.summary())
        self.link_label.setText(" · ".join(parts))
        tip = [MTU_TIP]
        if info:
            tip.insert(0, info.details())
        if s.priority:
            tip.append(f"Requested priority: {link.PRIORITIES[s.priority]}")
        self.link_label.setToolTip("\n".join(tip))

        bonded = s.bonded
        self.bond_label.setText({True: "🔒 Bonded", False: "Not bonded"}.get(bonded, ""))
        self.bond_label.setToolTip("Bond (pairing keys) stored by Windows" if bonded is not None else "")
        if s.pairing_busy:
            self.pair_btn.setText("Pairing…")
            self.pair_btn.setEnabled(False)
        elif bonded:
            self.pair_btn.setText("Unpair")
            self.pair_btn.setEnabled(True)
            self.pair_btn.setToolTip("Remove the bond. Windows also disconnects the device.")
        else:
            self.pair_btn.setText("Pair")
            self.pair_btn.setEnabled(connected)
            self.pair_btn.setToolTip("Bond with the device (Just Works, passkey or numeric comparison)")
        self.unpair_action.setEnabled(not s.pairing_busy and bonded is not False)

        can_prio = s.supports_priority
        self.priority_menu.setEnabled(can_prio)
        self.priority_menu.setToolTip(PRIORITY_TIP if can_prio else
                                      "Not available: needs Windows 11 and a connection")
        for key, act in self.priority_actions.items():
            act.setChecked(s.priority == key)
        self.phy_action.setToolTip(PHY_TIP + (f" Current: {info.tx_phy}." if info and info.tx_phy else ""))

    def _toggle_bond(self) -> None:
        if self.session.bonded:
            self._unpair()
        else:
            asyncio.ensure_future(self._pair())

    async def _pair(self) -> bool:
        prompt = PairingPrompt(self, self.session.name)
        try:
            return await self.session.pair(prompt)
        finally:
            prompt.close()

    def _unpair(self) -> None:
        asyncio.ensure_future(self.session.unpair())

    # -- tree ------------------------------------------------------------
    def _populate(self) -> None:
        self.tree.clear()
        self._items.clear()
        bold = QFont()
        bold.setBold(True)
        for svc in sorted(self.session.services, key=lambda s: s.handle):
            s_item = QTreeWidgetItem([uuids.display_name(svc.uuid, "services"),
                                      _uuid_text(svc.uuid), "", ""])
            s_item.setFont(COL_NAME, bold)
            s_item.setData(COL_NAME, KIND_ROLE, "service")
            s_item.setToolTip(COL_NAME, f"Handle 0x{svc.handle:04X}")
            self.tree.addTopLevelItem(s_item)
            for ch in sorted(svc.characteristics, key=lambda c: c.handle):
                c_item = QTreeWidgetItem([uuids.display_name(ch.uuid, "characteristics"),
                                          _uuid_text(ch.uuid), badges(ch.properties), ""])
                c_item.setData(COL_NAME, KIND_ROLE, "char")
                c_item.setData(COL_NAME, HANDLE_ROLE, ch.handle)
                c_item.setToolTip(COL_PROPS, ", ".join(ch.properties))
                c_item.setToolTip(COL_NAME, f"Handle 0x{ch.handle:04X}")
                s_item.addChild(c_item)
                self._items[ch.handle] = c_item
                self._formats.setdefault(ch.handle, default_format(ch.uuid))
                for d in sorted(ch.descriptors, key=lambda d: d.handle):
                    d_item = QTreeWidgetItem([uuids.display_name(d.uuid, "descriptors"),
                                              _uuid_text(d.uuid), "", ""])
                    d_item.setData(COL_NAME, KIND_ROLE, "cccd" if is_cccd(d) else "desc")
                    d_item.setData(COL_NAME, HANDLE_ROLE, d.handle)
                    d_item.setForeground(COL_NAME, QBrush(QColor(120, 120, 120)))
                    d_item.setToolTip(COL_NAME, f"Handle 0x{d.handle:04X}")
                    c_item.addChild(d_item)
                    self._items[d.handle] = d_item
                    fmt = ValueFormat.UTF8 if uuids.normalize(d.uuid) == uuids.normalize("2901") else ValueFormat.HEX
                    self._formats.setdefault(d.handle, fmt)
            s_item.setExpanded(True)
        # Values survive a reconnect: re-render what we already know.
        for handle in self._values:
            if handle in self._items:
                self._render_value(handle)
        self._update_actions()
        self._setup_tools()

    # -- tool pages -----------------------------------------------------------
    def _setup_tools(self) -> None:
        if supports_dfu(self.session):
            self.dfu_btn.show()
        if self.uart_view is None and has_nus(self.session):
            self.nus = NusClient(self.session, self)
            self.uart_view = UartView(self.nus)
            self.pages.insertTab(1, self.uart_view, "UART")
        if self.nus is not None and self._resume_nus:
            self._resume_nus = False
            asyncio.ensure_future(self.nus.start())

    def open_dfu(self) -> DfuDialog:
        if self.dfu_dialog is None:
            self.dfu_dialog = DfuDialog(self.session, self)
        self.dfu_dialog.show()
        self.dfu_dialog.raise_()
        return self.dfu_dialog

    def _on_page_changed(self, index: int) -> None:
        # Subscribe automatically the first time the terminal is opened;
        # after that the Enable button is the user's to control.
        if (self.pages.widget(index) is self.uart_view and self.nus is not None
                and not self._uart_opened and self.session.is_connected):
            self._uart_opened = True
            if not self.nus.active:
                asyncio.ensure_future(self.nus.start())

    def _selected(self) -> tuple[str | None, int | None]:
        item = self.tree.currentItem()
        if item is None:
            return None, None
        return item.data(COL_NAME, KIND_ROLE), item.data(COL_NAME, HANDLE_ROLE)

    def _update_actions(self) -> None:
        kind, handle = self._selected()
        connected = self.session.is_connected
        props: list[str] = []
        if kind == "char" and handle is not None:
            ch = self.session.characteristic(handle)
            props = ch.properties if ch else []
        is_desc = kind in ("desc", "cccd")
        self.read_btn.setEnabled(connected and ("read" in props or is_desc))
        self.write_btn.setEnabled(connected and (
            "write" in props or "write-without-response" in props or kind == "desc"))
        can_notify = "notify" in props or "indicate" in props
        self.notify_btn.setEnabled(connected and can_notify)
        on = handle in self.session.notifying
        self.notify_btn.setChecked(on)
        self.notify_btn.setText("Unsubscribe" if on else "Subscribe")
        self.format_combo.setEnabled(handle is not None)
        if handle is not None:
            fmt = self._formats.get(handle, ValueFormat.HEX)
            self.format_combo.setCurrentIndex(list(ValueFormat).index(fmt))
        if kind == "cccd":
            self.write_btn.setToolTip("Use Subscribe to change the CCCD")

    def _context_menu(self, pos) -> None:
        self.tree.setCurrentItem(self.tree.itemAt(pos))
        menu = QMenu(self)
        for btn in (self.read_btn, self.write_btn, self.notify_btn):
            act = menu.addAction(btn.text(), btn.click)
            act.setEnabled(btn.isEnabled())
        item = self.tree.currentItem()
        if item is not None:
            menu.addSeparator()
            menu.addAction("Copy UUID", lambda: _copy(item.text(COL_UUID)))
            if item.text(COL_VALUE):
                menu.addAction("Copy value", lambda: _copy(item.text(COL_VALUE)))
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    # -- operations ---------------------------------------------------------
    def _read_selected(self) -> None:
        kind, handle = self._selected()
        if handle is None or not self.read_btn.isEnabled():
            return
        if kind == "char":
            asyncio.ensure_future(self.session.read(handle))
        else:
            asyncio.ensure_future(self.session.read_descriptor(handle))

    def _write_selected(self) -> None:
        kind, handle = self._selected()
        if handle is None or not self.write_btn.isEnabled():
            return
        item = self._items[handle]
        if kind == "char":
            ch = self.session.characteristic(handle)
            if ch is None:
                return
            can_req, can_cmd = "write" in ch.properties, "write-without-response" in ch.properties
        else:
            can_req, can_cmd = True, False
        dlg = WriteDialog(item.text(COL_NAME), can_request=can_req, can_command=can_cmd,
                          default_format=self._formats.get(handle, ValueFormat.HEX), parent=self)
        if not dlg.exec():
            return
        if kind == "char":
            asyncio.ensure_future(self.session.write(handle, dlg.data, dlg.with_response))
        else:
            asyncio.ensure_future(self.session.write_descriptor(handle, dlg.data))

    def _toggle_notify_selected(self) -> None:
        _, handle = self._selected()
        if handle is None:
            return
        enable = handle not in self.session.notifying
        self.notify_btn.setEnabled(False)  # re-enabled by notify_changed / _update_actions

        async def run() -> None:
            await self.session.set_notify(handle, enable)
            self._update_actions()
        asyncio.ensure_future(run())

    async def _read_all(self) -> None:
        for svc in self.session.services:
            for ch in svc.characteristics:
                if "read" in ch.properties:
                    await self.session.read(ch.handle)

    def _format_changed(self, _index: int) -> None:
        _, handle = self._selected()
        if handle is None:
            return
        self._formats[handle] = ValueFormat(self.format_combo.currentData())
        self._render_value(handle)

    # -- value display --------------------------------------------------------
    def _on_value(self, handle: int, value: bytes, source: str) -> None:
        self._values[handle] = value
        if source == "notify":
            self._notify_counts[handle] = self._notify_counts.get(handle, 0) + 1
        item = self._items.get(handle)
        if item is not None:
            item.setToolTip(COL_VALUE, f"{source} at {time.strftime('%H:%M:%S')}")
            self._render_value(handle)

    def _render_value(self, handle: int) -> None:
        item = self._items.get(handle)
        if item is None or handle not in self._values:
            return
        text = decode(self._values[handle], self._formats.get(handle, ValueFormat.HEX))
        count = self._notify_counts.get(handle)
        if count:
            text = f"{text}    [{count} notifications]"
        item.setText(COL_VALUE, text)

    def _on_notify_changed(self, handle: int, enabled: bool) -> None:
        self._refresh_notify_marker(handle, enabled)
        self._update_actions()

    def _refresh_notify_marker(self, handle: int, enabled: bool) -> None:
        item = self._items.get(handle)
        if item is None or item.data(COL_NAME, KIND_ROLE) != "char":
            return
        ch_props = item.text(COL_PROPS).replace(" ⟳", "")
        item.setText(COL_PROPS, ch_props + (" ⟳" if enabled else ""))


def _uuid_text(uuid: str) -> str:
    short = uuids.short_form(uuid)
    return f"0x{short}" if len(short) <= 8 else short


def _copy(text: str) -> None:
    from PySide6.QtGui import QGuiApplication
    QGuiApplication.clipboard().setText(text)
