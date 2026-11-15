"""Blocking version of the API, for plain scripts, pytest and notebooks::

    from bluebench.api import sync as bb

    for d in bb.scan(timeout=5, mfr="5900"):
        print(d.address, d.name, d.rssi)

    with bb.connect(mfr="5900") as dev:
        print(dev.read("2a00"))
        with dev.notifications("2a37") as n:
            print(n.get(timeout=5))

All BLE work runs on one private asyncio loop in a background thread (the core's
Qt objects are created and used only there), so this
also works where an event loop is already running (Jupyter). Callbacks you pass
(``subscribe``, DFU ``progress``, sniffer ``on_packet``) are called on that thread.
Same arguments, results and exceptions as the async API.
"""
from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

# Import the functions, not the modules: the package re-exports ``scan`` and
# ``sniff`` under the module names.
from .device import Device, DfuResult, MacroResult, Service
from .device import connect as _connect
from .errors import NotConnected
from .scan import ScanResult
from .scan import find as _find
from .scan import scan as _scan
from .sniff import SniffResult
from .sniff import sniff as _sniff
from .sniff import sniffer_ports as _sniffer_ports

T = TypeVar("T")

_lock = threading.Lock()
_loop: asyncio.AbstractEventLoop | None = None


def _get_loop() -> asyncio.AbstractEventLoop:
    global _loop
    with _lock:
        if _loop is None or _loop.is_closed():
            loop = asyncio.new_event_loop()
            ready = threading.Event()

            def run() -> None:
                asyncio.set_event_loop(loop)
                loop.call_soon(ready.set)
                loop.run_forever()
            threading.Thread(target=run, name="bluebench-api", daemon=True).start()
            ready.wait()
            _loop = loop
        return _loop


def _run(coro: Coroutine[Any, Any, T]) -> T:
    loop = _get_loop()
    if threading.current_thread().name == "bluebench-api":
        raise RuntimeError("blocking BlueBench call from one of its own callbacks; "
                           "use the async API there")
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    try:
        return future.result()
    except KeyboardInterrupt:
        future.cancel()
        raise


async def _call(fn: Callable[[], Any]) -> Any:
    """Run a plain function on the loop thread (Qt objects live there)."""
    return fn()


# -- scanning -------------------------------------------------------------------
def scan(timeout: float = 5.0, flt=None, **criteria) -> list[ScanResult]:
    """See ``bluebench.api.scan``."""
    return _run(_scan(timeout, flt, **criteria))


def find(timeout: float = 10.0, flt=None, **criteria) -> ScanResult:
    """See ``bluebench.api.find``."""
    return _run(_find(timeout, flt, **criteria))


# -- devices ----------------------------------------------------------------------
class SyncNotifications:
    def __init__(self, ctx) -> None:
        self._ctx = ctx
        self._n = None

    def __enter__(self) -> "SyncNotifications":
        self._n = _run(self._ctx.__aenter__())
        return self

    def __exit__(self, *exc) -> None:
        _run(self._ctx.__aexit__(*exc))

    def get(self, timeout: float | None = None) -> bytes:
        """Next value; raises TimeoutError, or NotConnected once the link is gone."""
        return _run(self._n.get(timeout))

    def __iter__(self):
        while True:
            try:
                yield self.get()
            except NotConnected:
                return


class SyncUart:
    def __init__(self, uart) -> None:
        self._u = uart

    def __enter__(self) -> "SyncUart":
        _run(self._u.start())
        return self

    def __exit__(self, *exc) -> None:
        _run(self._u.stop())

    def send(self, data: bytes | str) -> None:
        _run(self._u.send(data))

    def receive(self, timeout: float | None = None) -> bytes:
        return _run(self._u.receive(timeout))

    def readline(self, timeout: float | None = None, sep: bytes = b"\n") -> bytes:
        return _run(self._u.readline(timeout, sep))


class SyncDevice:
    """Blocking wrapper of ``bluebench.api.Device``; ``.device`` is the async one."""

    def __init__(self, dev: Device) -> None:
        self.device = dev

    def __enter__(self) -> "SyncDevice":
        return self

    def __exit__(self, *exc) -> None:
        _run(self.device.__aexit__(*exc))

    def __repr__(self) -> str:
        return repr(self.device).replace("<Device", "<SyncDevice", 1)

    address = property(lambda self: self.device.address)
    name = property(lambda self: self.device.name)
    is_connected = property(lambda self: self.device.is_connected)
    mtu = property(lambda self: self.device.mtu)
    bonded = property(lambda self: self.device.bonded)
    link = property(lambda self: self.device.link)

    def disconnect(self) -> None:
        _run(self.device.disconnect())

    def services(self) -> list[Service]:
        return _run(_call(self.device.services))

    def read(self, char) -> bytes:
        return _run(self.device.read(char))

    def write(self, char, data, response: bool | None = None) -> None:
        _run(self.device.write(char, data, response))

    def read_descriptor(self, handle: int) -> bytes:
        return _run(self.device.read_descriptor(handle))

    def write_descriptor(self, handle: int, data) -> None:
        _run(self.device.write_descriptor(handle, data))

    def notifications(self, char, maxsize: int = 0) -> SyncNotifications:
        return SyncNotifications(self.device.notifications(char, maxsize))

    def subscribe(self, char, callback: Callable[[bytes], Any]) -> None:
        _run(self.device.subscribe(char, callback))

    def unsubscribe(self, char, callback: Callable[[bytes], Any] | None = None) -> None:
        _run(self.device.unsubscribe(char, callback))

    def pair(self, passkey: str | int | None = None, *, confirm: bool = True,
             prompt=None) -> str:
        return _run(self.device.pair(passkey, confirm=confirm, prompt=prompt))

    def unpair(self) -> None:
        _run(self.device.unpair())

    def uart(self) -> SyncUart:
        # Created on the loop thread: its Qt signals must not cross threads.
        return SyncUart(_run(_call(self.device.uart)))

    def run_macro(self, macro, *, name: str | None = None, repeat: int = 1,
                  stop_on_error: bool = True, check: bool = False) -> MacroResult:
        return _run(self.device.run_macro(macro, name=name, repeat=repeat,
                                          stop_on_error=stop_on_error, check=check))

    def dfu(self, package, *, prn: int = 0, progress=None) -> DfuResult:
        return _run(self.device.dfu(package, prn=prn, progress=progress))


def connect(target=None, *, flt=None, scan_timeout: float = 10.0, **criteria) -> SyncDevice:
    """See ``bluebench.api.connect``. Usable as ``with connect(...) as dev:``."""
    async def open_device() -> Device:
        return await _connect(target, flt=flt, scan_timeout=scan_timeout, **criteria)
    return SyncDevice(_run(open_device()))


# -- sniffer ------------------------------------------------------------------------
def sniffer_ports():
    return _sniffer_ports()


def sniff(port: str, duration: float, pcap_path=None, **kw) -> SniffResult:
    """See ``bluebench.api.sniff``."""
    return _run(_sniff(port, duration, pcap_path, **kw))
