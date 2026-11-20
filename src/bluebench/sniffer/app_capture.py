"""The "app" source of the Traffic tab: what BlueBench itself does.

No extra hardware: every GATT operation a DeviceSession performs (reads,
writes, subscriptions, notifications), connection changes and errors become
packets, so they can be filtered and inspected next to on-air traffic.
"""
from __future__ import annotations

from PySide6.QtCore import QObject

from ..ble import registry, uuids
from .ll_decode import _from_adv_field, hexs
from .model import CapturedPacket, Field, PacketStore

HOST = "BlueBench"


class AppCapture(QObject):
    def __init__(self, store: PacketStore, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.store = store
        self.enabled = True
        self._sessions: list = []

    def attach_session(self, session) -> None:
        if session in self._sessions:
            return
        self._sessions.append(session)
        session.operation.connect(lambda op, h, data, resp, s=session: self._on_operation(s, op, h, data, resp))
        session.value_updated.connect(lambda h, data, src, s=session: self._on_value(s, h, data, src))
        session.connection_changed.connect(lambda on, s=session: self._on_connection(s, on))
        session.log.connect(lambda level, msg, s=session: self._on_log(s, level, msg))
        session.destroyed.connect(lambda *_ , s=session: self._forget(s))

    def _forget(self, session) -> None:
        if session in self._sessions:
            self._sessions.remove(session)

    # -- helpers --------------------------------------------------------------
    @staticmethod
    def _peer(session) -> str:
        return session.address

    @staticmethod
    def _char(session, handle: int) -> tuple[str, str]:
        """(display text, uuid) of a characteristic handle."""
        try:
            ch = session.characteristic(handle)
        except Exception:
            ch = None
        if ch is None:
            return f"0x{handle:04X}", ""
        name = uuids.display_name(ch.uuid, "characteristics")
        return f"0x{handle:04X} ({name})", ch.uuid

    def _emit(self, session, outgoing: bool, protocol: str, info: str, raw: bytes = b"",
              tree: list[Field] | None = None, error: bool = False) -> None:
        if not self.enabled:
            return
        src, dst = (HOST, self._peer(session)) if outgoing else (self._peer(session), HOST)
        device = Field("Device", f"{session.name} · {session.address}")
        pkt = CapturedPacket("app", self.store.now(), src, dst, protocol, info, raw=bytes(raw),
                             tree=[device] + (tree or []), error=error)
        self.store.add(pkt)

    def _gatt_tree(self, title: str, session, handle: int, data: bytes | None) -> list[Field]:
        text, uuid = self._char(session, handle)
        f = Field("GATT", title)
        f.add("Characteristic", text)
        if uuid:
            f.add("UUID", uuid)
        if data is not None:
            f.add("Value", hexs(data) + (f'  "{data.decode()}"' if data and all(32 <= c < 127 for c in data) else ""),
                  span=(0, len(data)) if data else None)
            f.add("Length", f"{len(data)} bytes")
            if uuid:
                f.children += [_from_adv_field(x) for x in registry.decode_value(uuid, data)]
        return [f]

    # -- slots ------------------------------------------------------------------
    def _on_operation(self, session, op: str, handle: int, data: bytes, response: bool) -> None:
        text, _ = self._char(session, handle)
        if op == "write":
            title = "Write Request" if response else "Write Command"
            self._emit(session, True, "GATT", f"{title}, {text}, Value: {hexs(data[:16])}"
                       f"{'…' if len(data) > 16 else ''}", data,
                       self._gatt_tree(title, session, handle, data))
        elif op == "read":
            self._emit(session, False, "GATT", f"Read Response, {text}, Value: {hexs(data[:16])}"
                       f"{'…' if len(data) > 16 else ''}", data,
                       self._gatt_tree("Read", session, handle, data))
        elif op in ("subscribe", "unsubscribe"):
            title = "Enable notifications" if op == "subscribe" else "Disable notifications"
            self._emit(session, True, "GATT", f"{title}, {text}", b"",
                       self._gatt_tree(title, session, handle, None))

    def _on_value(self, session, handle: int, data: bytes, source: str) -> None:
        if source != "notify":
            return   # reads and writes arrive through `operation`
        text, _ = self._char(session, handle)
        self._emit(session, False, "GATT", f"Notification, {text}, Value: {hexs(data[:16])}"
                   f"{'…' if len(data) > 16 else ''}", data,
                   self._gatt_tree("Notification / Indication", session, handle, data))

    def _on_connection(self, session, connected: bool) -> None:
        info = "Connected" if connected else "Disconnected"
        tree = [Field("Link", info)]
        if connected and session.mtu:
            tree[0].add("ATT MTU", str(session.mtu))
            info += f", MTU {session.mtu}"
        self._emit(session, not connected, "LINK", info, b"", tree)

    def _on_log(self, session, level: str, msg: str) -> None:
        if level == "error":
            self._emit(session, False, "GATT", msg, b"", [Field("Error", msg)], error=True)
