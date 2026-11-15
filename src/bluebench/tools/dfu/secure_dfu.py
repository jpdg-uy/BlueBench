"""Nordic Secure DFU client (nRF5 SDK secure bootloader, SDK 12+).

Protocol summary (DFU service 0xFE59):
  Control point 8EC90001  write + notify. Requests are [opcode, args...];
                          responses are notifications [0x60, opcode, result, payload...].
  Packet        8EC90002  write without response: raw object data.
  Buttonless    8EC90003  (application) jump to the bootloader, which then advertises at
                          the device address + 1. 8EC90004 is the bonded variant, which
                          keeps the same address.

Each image is sent as an init-packet "command object" followed by firmware
"data objects" of at most ``max_size`` bytes (usually 4 KB). Every object is
created, filled, verified with CRC32 and executed. A transfer interrupted
part-way is resumed from the last verified offset.
"""
from __future__ import annotations

import asyncio
import random
import struct
import time
import zlib

from PySide6.QtCore import QObject, Signal

from ...ble import uuids
from ...ble.connection import DeviceSession
from .package import DfuImage, DfuPackage

SERVICE = uuids.normalize("fe59")
CONTROL_POINT = uuids.normalize("8ec90001-f315-4f60-9fb8-838830daea50")
PACKET = uuids.normalize("8ec90002-f315-4f60-9fb8-838830daea50")
BUTTONLESS = uuids.normalize("8ec90003-f315-4f60-9fb8-838830daea50")
BUTTONLESS_BONDED = uuids.normalize("8ec90004-f315-4f60-9fb8-838830daea50")

OP_CREATE, OP_SET_PRN, OP_CRC, OP_EXECUTE, OP_SELECT = 0x01, 0x02, 0x03, 0x04, 0x06
OP_RESPONSE = 0x60
OBJ_COMMAND, OBJ_DATA = 0x01, 0x02
BL_ENTER, BL_SET_NAME, BL_RESPONSE = 0x01, 0x02, 0x20

RESPONSE_TIMEOUT_S = 15.0
SCAN_TIMEOUT_S = 20.0
OBJECT_RETRIES = 3

RESULTS = {
    0x00: "invalid opcode", 0x02: "opcode not supported", 0x03: "invalid parameter",
    0x04: "insufficient resources", 0x05: "invalid object", 0x07: "unsupported type",
    0x08: "operation not permitted", 0x0A: "operation failed",
}
EXT_ERRORS = {
    0x01: "invalid error code", 0x02: "wrong command format", 0x03: "unknown command",
    0x04: "init packet is invalid", 0x05: "firmware version is too low (downgrade refused)",
    0x06: "hardware version mismatch", 0x07: "SoftDevice version mismatch",
    0x08: "init packet is not signed", 0x09: "unsupported hash type", 0x0A: "hash failed",
    0x0B: "unsupported signature type", 0x0C: "signature verification failed (wrong key?)",
    0x0D: "not enough space for the firmware",
}
BUTTONLESS_RESULTS = {
    0x02: "opcode not supported", 0x04: "operation failed", 0x05: "invalid advertising name",
    0x06: "busy", 0x07: "not bonded",
}


class DfuError(Exception):
    pass


def bootloader_address(address: str) -> str:
    """The address a non-bonded buttonless bootloader advertises at: address + 1."""
    value = (int(address.replace(":", ""), 16) + 1) & 0xFFFFFFFFFFFF
    text = f"{value:012X}"
    return ":".join(text[i:i + 2] for i in range(0, 12, 2))


def supports_dfu(session: DeviceSession) -> bool:
    return any(session.find_characteristic(u) is not None
               for u in (CONTROL_POINT, BUTTONLESS, BUTTONLESS_BONDED))


async def find_device(address: str, name: str | None, timeout: float = SCAN_TIMEOUT_S):
    """Scan for the bootloader by address or advertised name. Patched in tests."""
    from bleak import BleakScanner
    address = address.upper()

    def match(device, adv) -> bool:
        return device.address.upper() == address or (bool(name) and adv.local_name == name)
    return await BleakScanner.find_device_by_filter(match, timeout=timeout)


def chunk_size(session: DeviceSession, ch) -> int:
    size = getattr(ch, "max_write_without_response_size", None)
    if not size:
        mtu = session.mtu
        size = mtu - 3 if mtu else 20
    return max(20, int(size))


class _Responses:
    """Collects notifications/indications on one characteristic."""

    def __init__(self, session: DeviceSession, handle: int) -> None:
        self.session, self.handle = session, handle
        self.queue: asyncio.Queue[bytes] = asyncio.Queue()
        session.add_notify_listener(handle, self._on_data)

    def _on_data(self, _handle: int, data: bytes) -> None:
        self.queue.put_nowait(data)

    def close(self) -> None:
        self.session.remove_notify_listener(self.handle, self._on_data)

    async def wait(self, resp_code: int, opcode: int, timeout: float = RESPONSE_TIMEOUT_S) -> bytes:
        """Payload of the next [resp_code, opcode, result, ...] message; raises on errors."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise DfuError(f"no response to opcode 0x{opcode:02X} (timed out)")
            try:
                msg = await asyncio.wait_for(self.queue.get(), min(remaining, 0.5))
            except asyncio.TimeoutError:
                if not self.session.is_connected:
                    raise DfuError("the bootloader disconnected") from None
                continue
            if len(msg) < 3 or msg[0] != resp_code or msg[1] != opcode:
                continue  # stale or unrelated
            result = msg[2]
            if result == 0x01:
                return msg[3:]
            if resp_code == OP_RESPONSE and result == 0x0B and len(msg) > 3:
                raise DfuError(EXT_ERRORS.get(msg[3], f"extended error 0x{msg[3]:02X}"))
            table = RESULTS if resp_code == OP_RESPONSE else BUTTONLESS_RESULTS
            raise DfuError(table.get(result, f"error 0x{result:02X}"))


class SecureDfu(QObject):
    stage = Signal(str)
    progress = Signal(int, int)        # firmware bytes sent, total bytes (all images)
    log = Signal(str, str)
    finished = Signal(bool, str)

    def __init__(self, session: DeviceSession, package: DfuPackage, prn: int = 0,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self.package = package
        self.prn = prn
        self.running = False
        self._task: asyncio.Task | None = None
        self._target: DeviceSession | None = None
        self._owns_target = False
        self._sent_before = 0           # bytes of earlier images
        self.started_at = 0.0

    # -- control ----------------------------------------------------------
    def start(self) -> None:
        if not self.running:
            self.running = True  # before the task runs, so callers see it immediately
            self._task = asyncio.ensure_future(self.run())

    def abort(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()

    def _info(self, msg: str, level: str = "info") -> None:
        self.log.emit(level, msg)

    def _stage(self, text: str) -> None:
        self.stage.emit(text)
        self._info(text)

    async def run(self) -> bool:
        self.running = True
        self.started_at = time.monotonic()
        ok, summary = False, ""
        try:
            await self._run()
            elapsed = time.monotonic() - self.started_at
            kb = self.package.total_size / 1024
            ok, summary = True, f"DFU complete: {kb:.1f} KB in {elapsed:.1f} s"
        except DfuError as exc:
            summary = f"DFU failed: {exc}"
        except asyncio.CancelledError:
            summary = "DFU aborted. The device stays in bootloader mode until it times out " \
                      "or is updated."
        except Exception as exc:  # unexpected: report instead of leaving the dialog hanging
            summary = f"DFU failed: {type(exc).__name__}: {exc}"
        finally:
            await self._release_target()
            self.running = False
        self._info(summary, "info" if ok else "error")
        self.finished.emit(ok, summary)
        return ok

    # -- orchestration -------------------------------------------------------
    async def _run(self) -> None:
        s = self.session
        if not s.is_connected:
            raise DfuError("not connected")
        if s.find_characteristic(CONTROL_POINT) is not None:
            target_address, adv_name = s.address, None
            self._target = s
        else:
            target_address, adv_name = await self._enter_bootloader()
        for i, image in enumerate(self.package.images):
            if self._target is None:
                await self._connect_target(target_address, adv_name)
            label = f"{image.label} ({i + 1}/{len(self.package.images)})" \
                if len(self.package.images) > 1 else image.label
            await self._transfer(image, label)
            self._sent_before += len(image.firmware)
            # The bootloader resets after activating an image; later images
            # need a fresh connection to the bootloader.
            await self._release_target(wait_for_drop=True)

    async def _enter_bootloader(self) -> tuple[str, str | None]:
        s = self.session
        ch = s.find_characteristic(BUTTONLESS)
        bonded = ch is None
        if bonded:
            ch = s.find_characteristic(BUTTONLESS_BONDED)
        if ch is None:
            raise DfuError("the device has no DFU service (neither bootloader nor buttonless)")
        self._stage("Switching the device to bootloader mode…")
        if not await s.set_notify(ch.handle, True):
            raise DfuError("could not enable buttonless DFU indications")
        responses = _Responses(s, ch.handle)
        adv_name = None
        try:
            if not bonded:
                # A unique name lets us find the bootloader even if its address
                # is not the usual +1. Older firmware may not support this.
                name = f"Dfu{random.randrange(0x10000):04X}"
                if await s.write(ch.handle, bytes([BL_SET_NAME, len(name)]) + name.encode()):
                    try:
                        await responses.wait(BL_RESPONSE, BL_SET_NAME, timeout=5)
                        adv_name = name
                    except DfuError as exc:
                        self._info(f"Bootloader name not set ({exc}); finding it by address",
                                   "debug")
            s.expect_disconnect()
            if not await s.write(ch.handle, bytes([BL_ENTER])):
                if s.is_connected:
                    raise DfuError("could not send the enter-bootloader command")
            try:
                await responses.wait(BL_RESPONSE, BL_ENTER, timeout=5)
            except DfuError as exc:
                if s.is_connected and "timed out" not in str(exc):
                    raise DfuError(f"device refused to enter bootloader: {exc}") from None
        finally:
            responses.close()
        # The device resets now; wait for the link to drop.
        for _ in range(50):
            if not s.is_connected:
                break
            await asyncio.sleep(0.1)
        if s.is_connected:
            await s.disconnect()
        address = s.address if bonded else bootloader_address(s.address)
        return address, adv_name

    async def _connect_target(self, address: str, name: str | None) -> None:
        self._stage(f"Looking for the bootloader ({name or address})…")
        device = await find_device(address, name)
        if device is None:
            raise DfuError(f"bootloader not found (looked for {name or address} for "
                           f"{SCAN_TIMEOUT_S:.0f} s)")
        target = DeviceSession(device, "DFU bootloader", self)
        target.log.connect(lambda lvl, msg: self._info(f"[bootloader] {msg}", lvl))
        self._target, self._owns_target = target, True
        self._stage("Connecting to the bootloader…")
        for attempt in range(3):
            if await target.connect():
                break
            await asyncio.sleep(1.0)
        else:
            raise DfuError("could not connect to the bootloader")
        if target.find_characteristic(CONTROL_POINT) is None:
            raise DfuError("the device found is not a Secure DFU bootloader")

    async def _release_target(self, wait_for_drop: bool = False) -> None:
        target, self._target = self._target, None
        if target is None:
            return
        if wait_for_drop:
            for _ in range(30):  # the bootloader resets by itself after activation
                if not target.is_connected:
                    break
                await asyncio.sleep(0.1)
        if target.is_connected:
            target.expect_disconnect()
            await target.disconnect()
        if self._owns_target:
            target.deleteLater()
        self._owns_target = False

    # -- one image -------------------------------------------------------------
    async def _transfer(self, image: DfuImage, label: str) -> None:
        t = self._target
        assert t is not None
        cp = t.find_characteristic(CONTROL_POINT)
        pkt = t.find_characteristic(PACKET)
        if cp is None or pkt is None:
            raise DfuError("bootloader is missing the control point or packet characteristic")
        if not await t.set_notify(cp.handle, True):
            raise DfuError("could not enable control point notifications")
        self._cp, self._pkt = cp.handle, pkt.handle
        self._chunk = chunk_size(t, pkt)
        self._responses = _Responses(t, cp.handle)
        try:
            await self._request(bytes([OP_SET_PRN]) + struct.pack("<H", self.prn))
            self._stage(f"{label}: sending init packet…")
            await self._send_init(image.init_packet)
            self._stage(f"{label}: sending firmware ({len(image.firmware) / 1024:.1f} KB)…")
            t.expect_disconnect()  # the last execute resets the device
            await self._send_firmware(image.firmware)
            self._info(f"{label}: done")
        finally:
            self._responses.close()

    async def _request(self, payload: bytes, timeout: float = RESPONSE_TIMEOUT_S) -> bytes:
        t = self._target
        if t is None or not t.is_connected:
            raise DfuError("the bootloader disconnected")
        if not await t.write(self._cp, payload, response=True):
            if not t.is_connected:
                raise DfuError("the bootloader disconnected")
            raise DfuError(f"control point write failed (opcode 0x{payload[0]:02X})")
        return await self._responses.wait(OP_RESPONSE, payload[0], timeout)

    async def _select(self, obj_type: int) -> tuple[int, int, int]:
        r = await self._request(bytes([OP_SELECT, obj_type]))
        if len(r) < 12:
            raise DfuError("short Select response")
        return struct.unpack("<III", r[:12])  # max_size, offset, crc

    async def _crc(self) -> tuple[int, int]:
        r = await self._request(bytes([OP_CRC]))
        if len(r) < 8:
            raise DfuError("short checksum response")
        return struct.unpack("<II", r[:8])  # offset, crc

    async def _write_object(self, data: bytes, base: int, whole: bytes,
                            report: bool) -> None:
        """Send one object's bytes on the packet characteristic."""
        t = self._target
        n = 0
        for i in range(0, len(data), self._chunk):
            part = data[i:i + self._chunk]
            if not await t.write(self._pkt, part, response=False, quiet=True):
                raise DfuError("the bootloader disconnected" if not t.is_connected
                               else "packet write failed")
            n += 1
            if report:
                self.progress.emit(self._sent_before + base + i + len(part),
                                   self.package.total_size)
            if self.prn and n % self.prn == 0:
                # Unsolicited checksum every PRN packets: flow control + early check.
                r = await self._responses.wait(OP_RESPONSE, OP_CRC)
                off, crc = struct.unpack("<II", r[:8])
                end = base + i + len(part)
                if off != end or crc != zlib.crc32(whole[:end]):
                    raise _CrcMismatch(off)

    async def _send_init(self, init: bytes) -> None:
        max_size, offset, crc = await self._select(OBJ_COMMAND)
        if len(init) > max_size:
            raise DfuError(f"init packet is {len(init)} bytes; the bootloader accepts {max_size}")
        if offset == len(init) and crc == zlib.crc32(init):
            try:
                await self._request(bytes([OP_EXECUTE]))
                self._info("Init packet already received; resuming", "debug")
                return
            except DfuError:
                pass  # send it again
        for attempt in range(OBJECT_RETRIES):
            await self._request(bytes([OP_CREATE, OBJ_COMMAND]) + struct.pack("<I", len(init)))
            try:
                await self._write_object(init, 0, init, report=False)
            except _CrcMismatch:
                continue
            off, c = await self._crc()
            if off == len(init) and c == zlib.crc32(init):
                await self._request(bytes([OP_EXECUTE]))
                return
            self._info("Init packet CRC mismatch; retrying", "warning")
        raise DfuError("init packet failed CRC check repeatedly")

    async def _send_firmware(self, fw: bytes) -> None:
        max_size, offset, crc = await self._select(OBJ_DATA)
        if max_size == 0:
            raise DfuError("bootloader reported a zero object size")
        start = 0
        if 0 < offset <= len(fw) and crc == zlib.crc32(fw[:offset]):
            partial = offset % max_size
            if partial == 0 or offset == len(fw):
                # Whole objects received; the last one may not have been executed.
                try:
                    await self._request(bytes([OP_EXECUTE]))
                except DfuError:
                    pass
                start = offset
            else:
                start = offset - partial  # redo the half-sent object
            self._info(f"Resuming firmware transfer at {start} of {len(fw)} bytes")
            self.progress.emit(self._sent_before + start, self.package.total_size)
        for obj_start in range(start, len(fw), max_size):
            obj = fw[obj_start:obj_start + max_size]
            end = obj_start + len(obj)
            for attempt in range(OBJECT_RETRIES):
                await self._request(bytes([OP_CREATE, OBJ_DATA]) + struct.pack("<I", len(obj)))
                try:
                    await self._write_object(obj, obj_start, fw, report=True)
                except _CrcMismatch as exc:
                    self._info(f"CRC mismatch at offset {exc.args[0]}; resending object", "warning")
                    continue
                off, c = await self._crc()
                if off == end and c == zlib.crc32(fw[:end]):
                    break
                self._info(f"CRC mismatch at offset {off}; resending object", "warning")
            else:
                raise DfuError(f"object at offset {obj_start} failed CRC check repeatedly")
            # Executing writes the object to flash; the last one activates the image.
            last = end == len(fw)
            try:
                await self._request(bytes([OP_EXECUTE]), timeout=RESPONSE_TIMEOUT_S * (2 if last else 1))
            except DfuError as exc:
                # After the final execute some bootloaders reset before answering.
                reset_early = "disconnected" in str(exc) or "timed out" in str(exc)
                if not (last and reset_early):
                    raise


class _CrcMismatch(Exception):
    pass
