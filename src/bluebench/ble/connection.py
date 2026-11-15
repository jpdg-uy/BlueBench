"""DeviceSession: the single owner of a BleakClient for one peripheral.

The UI and every tool (NUS, macros, DFU) go through this class, so error
handling and logging live in one place. All BLE operations are coroutines on
the qasync loop; results are reported through Qt signals as well as returned.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable

from bleak import BleakClient
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.descriptor import BleakGATTDescriptor
from bleak.backends.device import BLEDevice
from bleak.backends.service import BleakGATTService
from PySide6.QtCore import QObject, Signal

from . import link, pairing, uuids
from .formats import to_hex

CONNECT_TIMEOUT_S = 20.0
CCCD_UUID = uuids.normalize("2902")

NotifyListener = Callable[[int, bytes], None]


class DeviceSession(QObject):
    # connected / disconnected (also fired on unexpected link loss)
    connection_changed = Signal(bool)
    # "connecting", "discovering", "connected", "disconnecting", "disconnected"
    state_changed = Signal(str)
    services_discovered = Signal()
    # handle, value, source ("read" | "write" | "notify")
    value_updated = Signal(int, bytes, str)
    notify_changed = Signal(int, bool)
    # True / False / None (platform cannot tell)
    bond_changed = Signal(object)
    pairing_changed = Signal(bool)  # a pair/unpair operation started / finished
    link_changed = Signal()  # connection parameters / PHY / priority
    # Successful characteristic operations, for the macro recorder:
    # op ("read" | "write" | "subscribe" | "unsubscribe"), handle, data, with_response
    operation = Signal(str, int, bytes, bool)
    # level ("debug" | "info" | "warning" | "error"), message
    log = Signal(str, str)

    def __init__(self, device: BLEDevice | str, name: str | None = None,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.device = device
        self.address = device if isinstance(device, str) else device.address
        self.name = name or (None if isinstance(device, str) else device.name) or "N/A"
        self.state = "disconnected"
        self.notifying: set[int] = set()
        self._client: BleakClient | None = None
        self._user_disconnect = False
        self._listeners: dict[int, list[NotifyListener]] = {}
        self._op_lock = asyncio.Lock()
        self.bonded: bool | None = None
        self.pairing_busy = False
        self.protection_level: str | None = None  # from the last successful pair()
        self.link: link.LinkInfo | None = None
        self.priority: str | None = None  # requested preset, see link.PRIORITIES
        self._priority_request = None
        self._stop_link_watch = lambda: None

    # -- state -------------------------------------------------------------
    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    @property
    def services(self) -> list[BleakGATTService]:
        if not self.is_connected:
            return []
        return list(self._client.services)  # type: ignore[union-attr]

    @property
    def mtu(self) -> int | None:
        if not self.is_connected:
            return None
        try:
            return self._client.mtu_size  # type: ignore[union-attr]
        except Exception:
            return None

    def characteristic(self, handle: int) -> BleakGATTCharacteristic | None:
        if not self.is_connected:
            return None
        return self._client.services.get_characteristic(handle)  # type: ignore[union-attr]

    def find_characteristic(self, uuid: str) -> BleakGATTCharacteristic | None:
        """First characteristic with this UUID (any service), for tools like NUS/DFU."""
        target = uuids.normalize(uuid)
        for svc in self.services:
            for ch in svc.characteristics:
                if uuids.normalize(ch.uuid) == target:
                    return ch
        return None

    def _set_state(self, state: str) -> None:
        self.state = state
        self.state_changed.emit(state)

    def _log(self, level: str, msg: str) -> None:
        self.log.emit(level, msg)

    # -- connection --------------------------------------------------------
    async def connect(self) -> bool:
        if self.is_connected or self.state in ("connecting", "discovering"):
            return self.is_connected
        self._user_disconnect = False
        self._set_state("connecting")
        self._log("info", f"Connecting to {self.address}…")
        # A fresh client per attempt: reusing one after a disconnect is
        # unreliable on some backends (notably WinRT).
        client = BleakClient(self.device, disconnected_callback=self._on_disconnected,
                             timeout=CONNECT_TIMEOUT_S)
        try:
            await client.connect()
        except Exception as exc:
            self._log("error", f"Connection failed: {_err(exc)}")
            self._set_state("disconnected")
            return False
        self._client = client
        self._set_state("discovering")
        # bleak performs service discovery as part of connect().
        n_svc = len(list(client.services))
        n_chr = sum(len(s.characteristics) for s in client.services)
        self._log("info", f"Connected. Discovered {n_svc} services, {n_chr} characteristics")
        mtu = self.mtu
        if mtu:
            self._log("debug", f"ATT MTU: {mtu}")
        loop = asyncio.get_running_loop()

        def on_link_change() -> None:  # runs on a WinRT thread
            try:
                loop.call_soon_threadsafe(self.refresh_link)
            except RuntimeError:  # loop closed during shutdown
                pass
        self._stop_link_watch = link.watch(client, on_link_change)
        self.refresh_link()
        if self.link:
            self._log("debug", "Link: " + self.link.details().replace("\n", ", "))
        self._set_state("connected")
        self.connection_changed.emit(True)
        self.services_discovered.emit()
        await self.refresh_bond()
        return True

    async def disconnect(self) -> None:
        client = self._client
        if client is None:
            return
        self._user_disconnect = True
        self._set_state("disconnecting")
        self._log("info", "Disconnecting…")
        try:
            await client.disconnect()
        except Exception as exc:
            self._log("warning", f"Disconnect error: {_err(exc)}")
        # Some backends do not fire the callback for a user-requested disconnect.
        if self._client is client:
            self._handle_disconnect()

    def expect_disconnect(self) -> None:
        """The peripheral is about to drop the link on purpose (DFU reset, unpair)."""
        self._user_disconnect = True

    def _on_disconnected(self, client: BleakClient) -> None:
        if client is self._client:
            self._handle_disconnect()

    def _handle_disconnect(self) -> None:
        self._stop_link_watch()
        self._stop_link_watch = lambda: None
        self._release_priority()
        self.link = None
        self._client = None
        self.notifying.clear()
        if self._user_disconnect:
            self._log("info", "Disconnected")
        else:
            self._log("warning", "Device disconnected unexpectedly")
        self._set_state("disconnected")
        self.connection_changed.emit(False)

    # -- bonding -------------------------------------------------------------
    def _set_bonded(self, bonded: bool | None) -> None:
        if bonded != self.bonded:
            self.bonded = bonded
            self.bond_changed.emit(bonded)

    def _set_pairing_busy(self, busy: bool) -> None:
        self.pairing_busy = busy
        self.pairing_changed.emit(busy)

    async def refresh_bond(self) -> bool | None:
        self._set_bonded(await pairing.bond_state(self._client, self.address))
        return self.bonded

    async def pair(self, prompt: pairing.Prompt) -> bool:
        if not self._require_connected() or self.pairing_busy:
            return False
        self._set_pairing_busy(True)
        self._log("info", "Pairing…")
        try:
            level = await pairing.pair(self._client, prompt, self._log)
        except pairing.PairingError as exc:
            self._log("error", f"Pairing failed: {exc}")
            return False
        finally:
            self._set_pairing_busy(False)
        self.protection_level = level
        self._log("info", f"Bonded ({level})")
        # Trust the OS's view when it has one; otherwise the success itself.
        state = await pairing.bond_state(self._client, self.address)
        self._set_bonded(True if state is None else state)
        return True

    async def unpair(self) -> bool:
        if self.pairing_busy:
            return False
        self._set_pairing_busy(True)
        self._log("info", "Removing bond…")
        # Windows drops the link when a bond is removed; that is expected.
        if self.is_connected:
            self._user_disconnect = True
        try:
            await pairing.unpair(self._client, self.address)
        except pairing.PairingError as exc:
            self._user_disconnect = False
            self._log("error", f"Unpair failed: {exc}")
            return False
        finally:
            self._set_pairing_busy(False)
        self._log("info", "Bond removed")
        self._set_bonded(False)
        return True

    # -- link parameters ------------------------------------------------------
    @property
    def supports_priority(self) -> bool:
        return self.is_connected and link.supports_priority(self._client)

    def refresh_link(self) -> None:
        info = link.read(self._client) if self.is_connected else None
        if info != self.link:
            if info and self.link:
                self._log("debug", "Link updated: " + info.details().replace("\n", ", "))
            self.link = info
            self.link_changed.emit()

    def request_priority(self, priority: str) -> bool:
        if not self._require_connected():
            return False
        label = link.PRIORITIES.get(priority, priority)
        try:
            req = link.request_priority(self._client, priority)
        except (RuntimeError, ValueError) as exc:
            self._log("error", f"Connection priority '{label}' failed: {exc}")
            return False
        self._release_priority()
        self._priority_request = req
        self.priority = priority
        self._log("info", f"Requested connection priority: {label}")
        self.link_changed.emit()
        return True

    def _release_priority(self) -> None:
        req, self._priority_request = self._priority_request, None
        self.priority = None
        if req is not None:
            try:
                req.close()
            except Exception:
                pass

    # -- GATT operations ------------------------------------------------------
    async def read(self, handle: int) -> bytes | None:
        ch = self._require_char(handle)
        if ch is None:
            return None
        self._log("debug", f"Reading {_label(ch)}")
        try:
            async with self._op_lock:
                value = bytes(await self._client.read_gatt_char(ch))  # type: ignore[union-attr]
        except Exception as exc:
            self._log("error", f"Read {_label(ch)} failed: {_err(exc)}")
            return None
        self._log("info", f"Read {_label(ch)}: {_fmt(value)}")
        self.value_updated.emit(handle, value, "read")
        self.operation.emit("read", handle, value, True)
        return value

    async def write(self, handle: int, data: bytes, response: bool = True,
                    quiet: bool = False) -> bool:
        """Write a characteristic. ``quiet`` (bulk transfers such as DFU) logs
        only failures and skips the value/recorder signals."""
        ch = self._require_char(handle)
        if ch is None:
            return False
        kind = "request" if response else "command"
        if not quiet:
            self._log("debug", f"Writing {_label(ch)} ({kind}): {_fmt(data)}")
        try:
            async with self._op_lock:
                await self._client.write_gatt_char(ch, data, response=response)  # type: ignore[union-attr]
        except Exception as exc:
            self._log("error", f"Write {_label(ch)} failed: {_err(exc)}")
            return False
        if quiet:
            return True
        self._log("info", f"Wrote {_label(ch)}: {_fmt(data)}")
        self.value_updated.emit(handle, bytes(data), "write")
        self.operation.emit("write", handle, bytes(data), response)
        return True

    async def set_notify(self, handle: int, enable: bool) -> bool:
        ch = self._require_char(handle)
        if ch is None:
            return False
        if enable == (handle in self.notifying):
            return True
        kind = "indications" if "notify" not in ch.properties else "notifications"
        try:
            async with self._op_lock:
                if enable:
                    await self._client.start_notify(ch, self._on_notify)  # type: ignore[union-attr]
                else:
                    await self._client.stop_notify(ch)  # type: ignore[union-attr]
        except Exception as exc:
            verb = "Enable" if enable else "Disable"
            self._log("error", f"{verb} {kind} on {_label(ch)} failed: {_err(exc)}")
            return False
        (self.notifying.add if enable else self.notifying.discard)(handle)
        self._log("info", f"{kind.capitalize()} {'enabled' if enable else 'disabled'} for {_label(ch)}")
        self.notify_changed.emit(handle, enable)
        self.operation.emit("subscribe" if enable else "unsubscribe", handle, b"", True)
        return True

    async def read_descriptor(self, handle: int) -> bytes | None:
        if not self._require_connected():
            return None
        try:
            async with self._op_lock:
                value = bytes(await self._client.read_gatt_descriptor(handle))  # type: ignore[union-attr]
        except Exception as exc:
            self._log("error", f"Read descriptor 0x{handle:04X} failed: {_err(exc)}")
            return None
        self._log("info", f"Read descriptor 0x{handle:04X}: {_fmt(value)}")
        self.value_updated.emit(handle, value, "read")
        return value

    async def write_descriptor(self, handle: int, data: bytes) -> bool:
        if not self._require_connected():
            return False
        try:
            async with self._op_lock:
                await self._client.write_gatt_descriptor(handle, data)  # type: ignore[union-attr]
        except Exception as exc:
            self._log("error", f"Write descriptor 0x{handle:04X} failed: {_err(exc)}")
            return False
        self._log("info", f"Wrote descriptor 0x{handle:04X}: {_fmt(data)}")
        self.value_updated.emit(handle, bytes(data), "write")
        return True

    # -- notification fan-out for tools --------------------------------------
    def add_notify_listener(self, handle: int, fn: NotifyListener) -> None:
        self._listeners.setdefault(handle, []).append(fn)

    def remove_notify_listener(self, handle: int, fn: NotifyListener) -> None:
        lst = self._listeners.get(handle, [])
        if fn in lst:
            lst.remove(fn)

    def _on_notify(self, ch: BleakGATTCharacteristic, data: bytearray) -> None:
        value = bytes(data)
        self._log("info", f"Notification from {_label(ch)}: {_fmt(value)}")
        self.value_updated.emit(ch.handle, value, "notify")
        for fn in list(self._listeners.get(ch.handle, [])):
            try:
                fn(ch.handle, value)
            except Exception as exc:  # a misbehaving tool must not break the session
                self._log("error", f"Notification listener error: {exc}")

    # -- helpers ----------------------------------------------------------
    def _require_connected(self) -> bool:
        if not self.is_connected:
            self._log("warning", "Not connected")
            return False
        return True

    def _require_char(self, handle: int) -> BleakGATTCharacteristic | None:
        if not self._require_connected():
            return None
        ch = self.characteristic(handle)
        if ch is None:
            self._log("error", f"No characteristic with handle 0x{handle:04X}")
        return ch


def is_cccd(desc: BleakGATTDescriptor) -> bool:
    return uuids.normalize(desc.uuid) == CCCD_UUID


def _label(ch: BleakGATTCharacteristic) -> str:
    name = uuids.name_for(ch.uuid, "characteristics")
    short = uuids.short_form(ch.uuid)
    return f"{name} ({short})" if name else short


def _fmt(data: bytes) -> str:
    return f"(0x) {to_hex(data)}" if data else "(empty)"


_SECURITY_WORDS = ("authentication", "authorization", "encryption")


def _err(exc: BaseException) -> str:
    text = str(exc) or type(exc).__name__
    if isinstance(exc, asyncio.TimeoutError):
        text = "timed out"
    elif any(w in text.lower() for w in _SECURITY_WORDS):
        text += " (the attribute needs a bond: try Pair)"
    return text
