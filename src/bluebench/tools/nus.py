"""Nordic UART Service (NUS) client on top of a DeviceSession.

NUS is a de-facto serial-over-BLE profile:
  RX 6E400002 — the central writes here (data *to* the peripheral)
  TX 6E400003 — the peripheral notifies here (data *from* the peripheral)
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from ..ble import uuids
from ..ble.connection import DeviceSession

NUS_SERVICE = uuids.normalize("6e400001-b5a3-f393-e0a9-e50e24dcca9e")
NUS_RX = uuids.normalize("6e400002-b5a3-f393-e0a9-e50e24dcca9e")
NUS_TX = uuids.normalize("6e400003-b5a3-f393-e0a9-e50e24dcca9e")

DEFAULT_CHUNK = 20  # ATT MTU 23 - 3, the safe minimum


def has_nus(session: DeviceSession) -> bool:
    return any(uuids.normalize(s.uuid) == NUS_SERVICE for s in session.services)


def chunks(data: bytes, size: int) -> list[bytes]:
    size = max(1, size)
    return [data[i:i + size] for i in range(0, len(data), size)] or [b""]


class NusClient(QObject):
    received = Signal(bytes)
    sent = Signal(bytes)
    active_changed = Signal(bool)
    error = Signal(str)

    def __init__(self, session: DeviceSession, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self.active = False
        self.rx_bytes = 0
        self.tx_bytes = 0
        self._tx_handle: int | None = None
        session.notify_changed.connect(self._on_notify_changed)
        session.connection_changed.connect(self._on_connection_changed)

    # -- lifecycle ---------------------------------------------------------
    async def start(self) -> bool:
        if self.active:
            return True
        tx = self.session.find_characteristic(NUS_TX)
        rx = self.session.find_characteristic(NUS_RX)
        if tx is None or rx is None:
            self.error.emit("This device has no Nordic UART Service")
            return False
        if self._tx_handle is not None:
            self.session.remove_notify_listener(self._tx_handle, self._on_data)
        self._tx_handle = tx.handle
        self.session.add_notify_listener(tx.handle, self._on_data)
        if not await self.session.set_notify(tx.handle, True):
            self.session.remove_notify_listener(tx.handle, self._on_data)
            self.error.emit("Could not enable notifications on NUS TX")
            return False
        self._set_active(True)
        return True

    async def stop(self) -> None:
        if self._tx_handle is not None:
            self.session.remove_notify_listener(self._tx_handle, self._on_data)
            if self.session.is_connected:
                await self.session.set_notify(self._tx_handle, False)
        self._set_active(False)

    def _set_active(self, on: bool) -> None:
        if on != self.active:
            self.active = on
            self.active_changed.emit(on)

    def _on_notify_changed(self, handle: int, enabled: bool) -> None:
        # The user may unsubscribe TX from the GATT tree.
        if handle == self._tx_handle and not enabled:
            self._set_active(False)

    def _on_connection_changed(self, connected: bool) -> None:
        if not connected:
            self._set_active(False)

    # -- data --------------------------------------------------------------
    def chunk_size(self) -> int:
        rx = self.session.find_characteristic(NUS_RX)
        size = getattr(rx, "max_write_without_response_size", None) if rx else None
        if not size:
            mtu = self.session.mtu
            size = mtu - 3 if mtu else DEFAULT_CHUNK
        return max(DEFAULT_CHUNK, int(size))

    async def send(self, data: bytes) -> bool:
        rx = self.session.find_characteristic(NUS_RX)
        if rx is None:
            self.error.emit("This device has no Nordic UART Service")
            return False
        # Announce first: a fast peripheral can reply before the last write
        # returns, and the terminal must show the input above the reply.
        self.sent.emit(data)
        # Write without response is much faster; fall back to requests.
        response = "write-without-response" not in rx.properties
        for part in chunks(data, self.chunk_size()):
            if not await self.session.write(rx.handle, part, response=response):
                self.error.emit("Send failed")
                return False
            self.tx_bytes += len(part)
        return True

    def _on_data(self, _handle: int, data: bytes) -> None:
        self.rx_bytes += len(data)
        self.received.emit(data)
