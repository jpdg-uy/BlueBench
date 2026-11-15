"""A fake nRF5 Secure DFU bootloader and a buttonless application for tests."""
from __future__ import annotations

import asyncio
import struct
import zlib

from bluebench.ble import uuids
from bluebench.tools.dfu.secure_dfu import BUTTONLESS, CONTROL_POINT, PACKET, SERVICE
from fakes import FakeChar, FakeClient, FakeDescriptor, FakeService, default_gatt

CP_HANDLE, PKT_HANDLE, BL_HANDLE = 0x30, 0x32, 0x40
CCCD = uuids.normalize("2902")


def bootloader_gatt():
    return [FakeService(SERVICE, 0x2E, [
        FakeChar(CONTROL_POINT, CP_HANDLE, ["write", "notify"], [FakeDescriptor(CCCD, 0x31)]),
        FakeChar(PACKET, PKT_HANDLE, ["write-without-response"], max_write_without_response_size=100),
    ])]


def app_gatt():
    return default_gatt() + [FakeService(SERVICE, 0x3E, [
        FakeChar(BUTTONLESS, BL_HANDLE, ["write", "indicate"], [FakeDescriptor(CCCD, 0x41)]),
    ])]


class FakeBootloader(FakeClient):
    gatt_factory = staticmethod(bootloader_gatt)
    max_data = 1024
    # Persist across connections like flash does (for resume tests).
    flash: dict = {}
    corrupt_once_at: int | None = None   # flip a byte of the packet at this data offset
    reject_init = False

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.prn = 0
        self.packets = 0
        self.kind = None
        f = FakeBootloader.flash
        f.setdefault("cmd", b"")
        f.setdefault("data", b"")
        f.setdefault("executed", 0)
        f.setdefault("activated", [])

    def _reply(self, opcode, result=1, payload=b""):
        if CP_HANDLE in self.notify_callbacks:
            self.push_notification(CP_HANDLE, bytes([0x60, opcode, result]) + payload)

    async def write_gatt_char(self, ch, data, response=True):
        data = bytes(data)
        f = FakeBootloader.flash
        if not self.is_connected:
            raise RuntimeError("Not connected")
        if ch.handle == PKT_HANDLE:
            await asyncio.sleep(0)  # a real radio write yields to the loop
            if self.kind == 1:
                f["cmd"] += data
            else:
                c = FakeBootloader.corrupt_once_at
                if c is not None and len(f["data"]) <= c < len(f["data"]) + len(data):
                    FakeBootloader.corrupt_once_at = None
                    i = c - len(f["data"])
                    data = data[:i] + bytes([data[i] ^ 0xFF]) + data[i + 1:]
                f["data"] += data
            self.packets += 1
            if self.prn and self.packets % self.prn == 0:
                buf = f["cmd"] if self.kind == 1 else f["data"]
                self._reply(0x03, payload=struct.pack("<II", len(buf), zlib.crc32(buf)))
            return
        if ch.handle != CP_HANDLE:
            return await super().write_gatt_char(ch, data, response)
        op = data[0]
        if op == 0x02:
            self.prn = struct.unpack("<H", data[1:3])[0]
            self._reply(op)
        elif op == 0x06:
            self.kind = data[1]  # Execute/CRC apply to the selected object type
            if data[1] == 1:
                buf, mx = f["cmd"], 256
            else:
                buf, mx = f["data"], self.max_data
            self._reply(op, payload=struct.pack("<III", mx, len(buf), zlib.crc32(buf)))
        elif op == 0x01:
            self.kind = data[1]
            self.packets = 0
            if self.kind == 1:
                f["cmd"] = b""
            else:
                f["data"] = f["data"][:f["executed"]]
            self._reply(op)
        elif op == 0x03:
            buf = f["cmd"] if self.kind == 1 else f["data"]
            self._reply(op, payload=struct.pack("<II", len(buf), zlib.crc32(buf)))
        elif op == 0x04:
            if self.kind == 1:
                if FakeBootloader.reject_init:
                    self._reply(op, 0x0B, b"\x0c")  # signature verification failed
                    return
                # Like the SDK bootloader: re-executing the same init packet
                # keeps the firmware progress, so transfers can resume.
                if f["cmd"] != f.get("validated"):
                    f["data"], f["executed"] = b"", 0
                f["validated"] = f["cmd"]
                self._reply(op)
            else:
                f["executed"] = len(f["data"])
                self._reply(op)
                if f["executed"] == f.get("expected"):
                    f["activated"].append(f["data"])
                    asyncio.get_running_loop().call_later(0.01, self._reset)
        else:
            self._reply(op, 0x02)

    def _reset(self):
        if self.is_connected:
            self.drop_link()


class FakeApp(FakeClient):
    gatt_factory = staticmethod(app_gatt)
    named: list = []

    async def write_gatt_char(self, ch, data, response=True):
        data = bytes(data)
        if ch.handle != BL_HANDLE:
            return await super().write_gatt_char(ch, data, response)
        if data[0] == 0x02:
            FakeApp.named.append(data[2:2 + data[1]].decode())
            self.push_notification(BL_HANDLE, b"\x20\x02\x01")
        elif data[0] == 0x01:
            self.push_notification(BL_HANDLE, b"\x20\x01\x01")
            asyncio.get_running_loop().call_later(0.01, self.drop_link)
