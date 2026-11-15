"""A connected peripheral: GATT, notifications, pairing, UART, macros and DFU."""
from __future__ import annotations

import asyncio
import inspect
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..ble import uuids
from ..ble.adv_filter import DeviceFilter
from ..ble.connection import DeviceSession
from ..ble.formats import ValueFormat, encode
from ..ble.link import LinkInfo
from .errors import (ConnectFailed, DeviceNotFound, DfuFailed, GattError, MacroFailed,
                     NotConnected, PairingFailed)
from ._logging import LogTap, logger
from .scan import ScanResult, find, make_filter

_ADDRESS = re.compile(r"^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$|^[0-9A-Fa-f-]{36}$")


def is_address(text: str) -> bool:
    """A Bluetooth address (AA:BB:CC:DD:EE:FF), or the UUID macOS uses instead."""
    return bool(_ADDRESS.match(text.strip()))


def to_bytes(data: Any) -> bytes:
    if isinstance(data, str):
        return encode(data, ValueFormat.HEX)
    return bytes(data)


# -- GATT description ---------------------------------------------------------
@dataclass(frozen=True)
class Descriptor:
    uuid: str
    name: str
    handle: int

    def to_dict(self) -> dict:
        return {"uuid": uuids.short_form(self.uuid), "name": self.name, "handle": self.handle}


@dataclass(frozen=True)
class Characteristic:
    uuid: str
    name: str
    handle: int
    properties: tuple[str, ...]
    descriptors: tuple[Descriptor, ...] = ()

    def to_dict(self) -> dict:
        return {"uuid": uuids.short_form(self.uuid), "name": self.name, "handle": self.handle,
                "properties": list(self.properties),
                "descriptors": [d.to_dict() for d in self.descriptors]}


@dataclass(frozen=True)
class Service:
    uuid: str
    name: str
    handle: int
    characteristics: tuple[Characteristic, ...] = ()

    def to_dict(self) -> dict:
        return {"uuid": uuids.short_form(self.uuid), "name": self.name, "handle": self.handle,
                "characteristics": [c.to_dict() for c in self.characteristics]}


# -- results ----------------------------------------------------------------
@dataclass
class StepResult:
    index: int          # 0-based step index in the macro
    run: int            # 0-based repetition
    description: str
    ok: bool
    error: str | None = None

    def to_dict(self) -> dict:
        return {"step": self.index + 1, "run": self.run + 1, "description": self.description,
                "ok": self.ok, "error": self.error}


@dataclass
class MacroResult:
    name: str
    ok: bool
    summary: str
    steps: list[StepResult] = field(default_factory=list)
    seconds: float = 0.0

    def to_dict(self) -> dict:
        return {"macro": self.name, "ok": self.ok, "summary": self.summary,
                "seconds": round(self.seconds, 3), "steps": [s.to_dict() for s in self.steps]}


@dataclass
class DfuResult:
    ok: bool
    summary: str
    bytes: int
    seconds: float

    def to_dict(self) -> dict:
        return {"ok": self.ok, "summary": self.summary, "bytes": self.bytes,
                "seconds": round(self.seconds, 3)}


# -- notifications ----------------------------------------------------------
_CLOSED = object()


class Notifications:
    """Queue of values notified on one characteristic. Use through
    ``async with device.notifications(char) as n:`` then ``await n.get()`` or
    ``async for value in n``. Iteration ends when the device disconnects."""

    def __init__(self, device: "Device", handle: int, maxsize: int = 0) -> None:
        self._device = device
        self.handle = handle
        self._queue: asyncio.Queue = asyncio.Queue(maxsize)
        self.dropped = 0

    def _put(self, _handle: int, value: bytes) -> None:
        try:
            self._queue.put_nowait(value)
        except asyncio.QueueFull:
            self.dropped += 1

    def _closed(self, connected: bool) -> None:
        if not connected:
            self._queue.put_nowait(_CLOSED)

    async def get(self, timeout: float | None = None) -> bytes:
        """Next value. Raises TimeoutError, or NotConnected once the link is gone."""
        try:
            item = await asyncio.wait_for(self._queue.get(), timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(f"No notification within {timeout:g} s") from None
        if item is _CLOSED:
            self._queue.put_nowait(_CLOSED)  # keep later calls failing too
            raise NotConnected("Device disconnected")
        return item

    def __aiter__(self) -> "Notifications":
        return self

    async def __anext__(self) -> bytes:
        try:
            return await self.get()
        except NotConnected:
            raise StopAsyncIteration from None


class _NotifyContext:
    def __init__(self, device: "Device", char, maxsize: int) -> None:
        self._device, self._char, self._maxsize = device, char, maxsize
        self._n: Notifications | None = None

    async def __aenter__(self) -> Notifications:
        d = self._device
        ch = d._char(self._char, "notify", "indicate")
        n = Notifications(d, ch.handle, self._maxsize)
        d.session.add_notify_listener(ch.handle, n._put)
        d.session.connection_changed.connect(n._closed)
        try:
            await d._set_notify(ch.handle, True)
        except BaseException:
            self._detach(n)
            raise
        self._n = n
        return n

    def _detach(self, n: Notifications) -> None:
        s = self._device.session
        s.remove_notify_listener(n.handle, n._put)
        try:
            s.connection_changed.disconnect(n._closed)
        except (RuntimeError, TypeError):
            pass

    async def __aexit__(self, *exc) -> None:
        n, self._n = self._n, None
        if n is None:
            return
        self._detach(n)
        s = self._device.session
        if s.is_connected and not s._listeners.get(n.handle) and not self._device._callbacks.get(n.handle):
            await s.set_notify(n.handle, False)


# -- UART -----------------------------------------------------------------
class Uart:
    """Nordic UART Service. Use through ``async with device.uart() as uart:``."""

    def __init__(self, device: "Device") -> None:
        from ..tools.nus import NusClient
        self._device = device
        self._nus = NusClient(device.session)
        self._buffer = bytearray()
        self._arrived = asyncio.Event()
        self._errors: list[str] = []
        self._nus.received.connect(self._on_data)
        self._nus.error.connect(self._errors.append)

    def _on_data(self, data: bytes) -> None:
        self._buffer += data
        self._arrived.set()

    async def start(self) -> "Uart":
        self._device._require_connected()
        if not await self._nus.start():
            raise GattError(self._errors[-1] if self._errors else "Could not start UART")
        return self

    async def stop(self) -> None:
        await self._nus.stop()

    async def __aenter__(self) -> "Uart":
        return await self.start()

    async def __aexit__(self, *exc) -> None:
        await self.stop()

    async def send(self, data: bytes | str) -> None:
        """Send bytes, or text (encoded as UTF-8). Split to fit the MTU."""
        self._device._require_connected()
        payload = data.encode("utf-8") if isinstance(data, str) else bytes(data)
        self._errors.clear()
        if not await self._nus.send(payload):
            raise GattError(self._device._tap.error(self._errors[-1] if self._errors
                                                    else "UART send failed"))

    async def _wait(self, timeout: float | None) -> bool:
        self._arrived.clear()
        try:
            await asyncio.wait_for(self._arrived.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def receive(self, timeout: float | None = None) -> bytes:
        """Everything received so far; waits up to ``timeout`` for at least one byte.
        Returns b"" if nothing arrived in time."""
        if not self._buffer:
            await self._wait(timeout)
        data, self._buffer = bytes(self._buffer), bytearray()
        return data

    async def readline(self, timeout: float | None = None, sep: bytes = b"\n") -> bytes:
        """One line, including ``sep``. Raises TimeoutError if none is complete in time."""
        loop = asyncio.get_running_loop()
        deadline = None if timeout is None else loop.time() + timeout
        while (i := self._buffer.find(sep)) < 0:
            remaining = None if deadline is None else deadline - loop.time()
            if (remaining is not None and remaining <= 0) or not await self._wait(remaining):
                raise TimeoutError(f"No complete line within {timeout:g} s")
        end = i + len(sep)
        line, self._buffer = bytes(self._buffer[:end]), self._buffer[end:]
        return line

    def __aiter__(self) -> "Uart":
        return self

    async def __anext__(self) -> bytes:
        while not self._buffer:
            if not self._device.is_connected:
                raise StopAsyncIteration
            await self._wait(0.5)
        data, self._buffer = bytes(self._buffer), bytearray()
        return data


# -- the device -------------------------------------------------------------
PairPrompt = Callable[[str, str], "Awaitable[str | bool | None] | str | bool | None"]


class Device:
    """A peripheral and its connection. Create with ``connect()``.

    Every failing operation raises a BlueBenchError subclass with the reason.
    """

    def __init__(self, session: DeviceSession) -> None:
        self.session = session
        self._tap = LogTap(session.log, session.address)
        self._callbacks: dict[int, list[Callable[[bytes], Any]]] = {}

    # -- connection -----------------------------------------------------------
    @property
    def address(self) -> str:
        return self.session.address

    @property
    def name(self) -> str:
        return self.session.name

    @property
    def is_connected(self) -> bool:
        return self.session.is_connected

    @property
    def mtu(self) -> int | None:
        return self.session.mtu

    @property
    def bonded(self) -> bool | None:
        """True/False if the OS can tell, None otherwise."""
        return self.session.bonded

    @property
    def link(self) -> LinkInfo | None:
        """Connection interval / PHY when the OS reports them (Windows 11)."""
        return self.session.link

    async def _connect(self) -> None:
        self._tap.reset()
        if not await self.session.connect():
            msg = self._tap.error("Connection failed")
            cls = DeviceNotFound if "not found" in msg.lower() else ConnectFailed
            raise cls(f"{self.address}: {msg}")

    async def disconnect(self) -> None:
        await self.session.disconnect()

    async def __aenter__(self) -> "Device":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.disconnect()
        self._tap.close()

    def __repr__(self) -> str:
        state = "connected" if self.is_connected else "disconnected"
        return f"<Device {self.address} {self.name!r} {state}>"

    # -- GATT -----------------------------------------------------------------
    def _require_connected(self) -> None:
        if not self.session.is_connected:
            raise NotConnected(f"{self.address} is not connected")

    def _char(self, char, *needs: str):
        """The bleak characteristic for a UUID or handle; checks one of ``needs``."""
        self._require_connected()
        if isinstance(char, int):
            ch = self.session.characteristic(char)
        else:
            ch = self.session.find_characteristic(str(char))
        if ch is None:
            what = f"handle 0x{char:04X}" if isinstance(char, int) else uuids.short_form(
                uuids.normalize(str(char)))
            raise GattError(f"{self.address} has no characteristic {what}")
        if needs and not any(p in ch.properties for p in needs):
            raise GattError(f"{uuids.display_name(ch.uuid, 'characteristics')} does not support "
                            f"{' or '.join(needs)} (it has: {', '.join(ch.properties)})")
        return ch

    def services(self) -> list[Service]:
        """The GATT database discovered at connection time."""
        self._require_connected()
        out = []
        for svc in sorted(self.session.services, key=lambda s: s.handle):
            chars = tuple(
                Characteristic(ch.uuid, uuids.display_name(ch.uuid, "characteristics"), ch.handle,
                               tuple(ch.properties),
                               tuple(Descriptor(d.uuid, uuids.display_name(d.uuid, "descriptors"),
                                                d.handle)
                                     for d in sorted(ch.descriptors, key=lambda d: d.handle)))
                for ch in sorted(svc.characteristics, key=lambda c: c.handle))
            out.append(Service(svc.uuid, uuids.display_name(svc.uuid, "services"), svc.handle,
                               chars))
        return out

    async def read(self, char) -> bytes:
        ch = self._char(char, "read")
        self._tap.reset()
        value = await self.session.read(ch.handle)
        if value is None:
            raise self._gatt_error("Read failed")
        return value

    async def write(self, char, data, response: bool | None = None) -> None:
        """Write bytes (or a hex string). ``response`` None picks a write request when
        the characteristic supports it, else a write command."""
        ch = self._char(char, "write", "write-without-response")
        payload = to_bytes(data)
        if response is None:
            response = "write" in ch.properties
        self._tap.reset()
        if not await self.session.write(ch.handle, payload, response=response):
            raise self._gatt_error("Write failed")

    async def read_descriptor(self, handle: int) -> bytes:
        self._require_connected()
        self._tap.reset()
        value = await self.session.read_descriptor(handle)
        if value is None:
            raise self._gatt_error("Descriptor read failed")
        return value

    async def write_descriptor(self, handle: int, data) -> None:
        self._require_connected()
        self._tap.reset()
        if not await self.session.write_descriptor(handle, to_bytes(data)):
            raise self._gatt_error("Descriptor write failed")

    def _gatt_error(self, fallback: str) -> Exception:
        if not self.session.is_connected:
            return NotConnected(self._tap.error(f"{self.address} disconnected"))
        return GattError(self._tap.error(fallback))

    async def _set_notify(self, handle: int, enable: bool) -> None:
        self._tap.reset()
        if not await self.session.set_notify(handle, enable):
            raise self._gatt_error("Could not change notifications")

    def notifications(self, char, maxsize: int = 0) -> _NotifyContext:
        """``async with dev.notifications("2a37") as n: value = await n.get(timeout=5)``"""
        return _NotifyContext(self, char, maxsize)

    async def subscribe(self, char, callback: Callable[[bytes], Any]) -> None:
        """Call ``callback(value)`` for every notification/indication on ``char``.
        Coroutine functions are scheduled as tasks."""
        ch = self._char(char, "notify", "indicate")
        cbs = self._callbacks.setdefault(ch.handle, [])
        if not cbs:
            self.session.add_notify_listener(ch.handle, self._dispatch)
        cbs.append(callback)
        try:
            await self._set_notify(ch.handle, True)
        except BaseException:
            await self.unsubscribe(char, callback)
            raise

    async def unsubscribe(self, char, callback: Callable[[bytes], Any] | None = None) -> None:
        """Remove one callback (or all of them) and stop notifications when none is left."""
        ch = self._char(char) if self.is_connected else None
        handle = ch.handle if ch is not None else (char if isinstance(char, int) else None)
        if handle is None:
            return
        cbs = self._callbacks.get(handle, [])
        if callback is None:
            cbs.clear()
        elif callback in cbs:
            cbs.remove(callback)
        if not cbs:
            self._callbacks.pop(handle, None)
            self.session.remove_notify_listener(handle, self._dispatch)
            if self.is_connected and not self.session._listeners.get(handle):
                await self.session.set_notify(handle, False)

    def _dispatch(self, handle: int, value: bytes) -> None:
        for cb in list(self._callbacks.get(handle, [])):
            result = cb(value)
            if inspect.isawaitable(result):
                asyncio.ensure_future(result)

    # -- pairing -------------------------------------------------------------
    async def pair(self, passkey: str | int | None = None, *, confirm: bool = True,
                   prompt: PairPrompt | None = None) -> str:
        """Pair (bond). Returns the protection level the OS used.

        passkey  answer for devices that display a passkey ("provide_pin")
        confirm  answer for numeric comparison ("confirm_pin")
        prompt   optional ``prompt(kind, pin)`` (sync or async) that decides instead;
                 kinds are confirm, display_pin, provide_pin, confirm_pin
        """
        self._require_connected()

        async def answer(kind: str, pin: str) -> str | bool | None:
            if prompt is not None:
                reply = prompt(kind, pin)
                return await reply if inspect.isawaitable(reply) else reply
            if kind == "provide_pin":
                if passkey is None:
                    logger.error("%s: the device asks for a passkey; pass passkey=",
                                 self.address)
                return None if passkey is None else str(passkey)
            if kind == "display_pin":
                logger.warning("%s: enter passkey %s on the device", self.address, pin)
                return True
            if kind == "confirm_pin":
                logger.info("%s: numeric comparison %s -> %s", self.address, pin,
                            "accept" if confirm else "reject")
                return confirm
            return True

        self._tap.reset()
        if not await self.session.pair(answer):
            raise PairingFailed(self._tap.error("Pairing failed"))
        return self.session.protection_level or "unknown"

    async def unpair(self) -> None:
        """Remove the bond. On Windows this also drops the connection."""
        self._tap.reset()
        if not await self.session.unpair():
            raise PairingFailed(self._tap.error("Removing the bond failed"))

    # -- tools ----------------------------------------------------------------
    def uart(self) -> Uart:
        """Nordic UART Service: ``async with dev.uart() as u: await u.send("hi\\n")``."""
        return Uart(self)

    async def run_macro(self, macro, *, name: str | None = None, repeat: int = 1,
                        stop_on_error: bool = True, check: bool = False) -> MacroResult:
        """Run a macro: a Macro, the name of one in the user's library, or a path
        to an exported .json file (``name`` picks one if the file holds several).
        Returns the result; ``check=True`` raises MacroFailed when it fails."""
        from ..core.macros import MacroRunner
        m = load_macro(macro, name)
        self._require_connected()
        runner = MacroRunner(self.session)
        steps: list[StepResult] = []
        run = {"n": 0, "last": -1}

        def settle() -> None:
            # The runner logs why a step failed right after step_finished.
            if steps and not steps[-1].ok and steps[-1].error is None:
                steps[-1].error = (self._tap.first_error or "failed").split(" failed: ", 1)[-1]

        def started(i: int) -> None:
            settle()
            if i <= run["last"]:
                run["n"] += 1
            run["last"] = i
            self._tap.reset()

        def finished(i: int, ok: bool) -> None:
            steps.append(StepResult(i, run["n"], m.steps[i].describe(), ok))

        runner.step_started.connect(started)
        runner.step_finished.connect(finished)
        summary = {"text": ""}
        runner.finished.connect(lambda _ok, text: summary.__setitem__("text", text))
        t0 = time.monotonic()
        ok = await runner.run(m, repeat=repeat, stop_on_error=stop_on_error)
        settle()
        result = MacroResult(m.name, ok, summary["text"], steps, time.monotonic() - t0)
        if check and not ok:
            raise MacroFailed(f"Macro '{m.name}' {result.summary}")
        return result

    async def dfu(self, package: str | Path, *, prn: int = 0,
                  progress: Callable[[int, int], Any] | None = None) -> DfuResult:
        """Secure DFU with a Nordic .zip package. ``progress(sent, total)`` is called
        as firmware bytes go out. Raises DfuFailed. The device reboots afterwards,
        so this Device ends disconnected."""
        from ..tools.dfu.package import PackageError, load
        from ..tools.dfu.secure_dfu import SecureDfu
        try:
            pkg = load(package)
        except (PackageError, OSError) as exc:
            raise DfuFailed(f"Invalid DFU package {package}: {exc}") from None
        self._require_connected()
        dfu = SecureDfu(self.session, pkg, prn=prn)
        tap = LogTap(dfu.log, f"{self.address} DFU")
        dfu.stage.connect(lambda text: logger.info("%s DFU: %s", self.address, text))
        if progress is not None:
            dfu.progress.connect(progress)
        summary = {"text": ""}
        dfu.finished.connect(lambda _ok, text: summary.__setitem__("text", text))
        t0 = time.monotonic()
        try:
            ok = await dfu.run()
        finally:
            tap.close()
        if not ok:
            raise DfuFailed(summary["text"] or "DFU failed")
        return DfuResult(True, summary["text"], pkg.total_size, time.monotonic() - t0)


def load_macro(macro, name: str | None = None):
    """A Macro from a Macro, a library name or a .json file path."""
    from ..core import macros as m
    if isinstance(macro, m.Macro):
        return macro
    text = str(macro)
    path = Path(text)
    if path.suffix.lower() == ".json" or path.exists():
        try:
            in_file = m.loads(path.read_text("utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise MacroFailed(f"Could not load macros from {path}: {exc}") from None
        picked = [x for x in in_file if name is None or x.name == name]
        if len(picked) == 1:
            return picked[0]
        names = ", ".join(repr(x.name) for x in in_file) or "no macros"
        if name is None:
            raise MacroFailed(f"{path} holds {names}; pick one with name=")
        raise MacroFailed(f"No macro named {name!r} in {path} (it has {names})")
    lib = m.library()
    if lib.load_error:
        raise MacroFailed(lib.load_error)
    for x in lib.macros:
        if x.name == text:
            return x
    names = ", ".join(repr(x.name) for x in lib.macros) or "none"
    raise MacroFailed(f"No macro named {text!r} in the library (saved macros: {names})")


# -- connecting ------------------------------------------------------------
CONNECT_TIMEOUT_S = 20.0


class _Connect:
    """``await connect(...)`` or ``async with connect(...) as dev:`` (disconnects at the end)."""

    def __init__(self, factory: Callable[[], Awaitable[Device]]) -> None:
        self._factory = factory
        self._device: Device | None = None

    def __await__(self):
        return self._factory().__await__()

    async def __aenter__(self) -> Device:
        self._device = await self._factory()
        return self._device

    async def __aexit__(self, *exc) -> None:
        if self._device is not None:
            await self._device.__aexit__(*exc)


def connect(target: str | ScanResult | None = None, *, flt: DeviceFilter | None = None,
            scan_timeout: float = 10.0, **criteria) -> _Connect:
    """Connect to a device and discover its services.

    target    an address, an exact device name, or a ScanResult from scan()/find()
    criteria  instead of target: name (substring), mfr ("5900"), service, min_rssi...
              the first matching device found within ``scan_timeout`` is used
    Raises DeviceNotFound, ConnectFailed or BluetoothUnavailable.
    """
    if target is None and flt is None and not criteria:
        raise TypeError("connect() needs a target (address, name or ScanResult) or criteria")

    async def open_device() -> Device:
        device: Any
        name = None
        if isinstance(target, ScanResult):
            device, name = target.device or target.address, target.name
        elif isinstance(target, str) and is_address(target):
            device = target.strip().upper() if ":" in target else target.strip()
        else:
            f = make_filter(flt, **criteria) if target is None else \
                DeviceFilter(name=str(target), exact_name=True)
            found = await find(scan_timeout, f)
            device, name = found.device or found.address, found.name
        session = DeviceSession(device, name)
        dev = Device(session)
        try:
            await dev._connect()
        except BaseException:
            dev._tap.close()
            raise
        return dev

    return _Connect(open_device)
