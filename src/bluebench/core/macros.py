"""Macros: recorded or hand-written sequences of GATT operations.

Steps refer to characteristics by UUID, not handle, so a macro recorded on one
board runs on another with the same firmware. A shared library lives in
``~/.bluebench/macros.json``; single macros can be exported/imported too.

Step ops:
  write        write ``data`` (hex) to ``uuid``; ``response`` picks request/command
  read         read ``uuid``; if ``data`` is set it is the expected value pattern
  subscribe    enable notifications/indications on ``uuid``
  unsubscribe  disable them
  expect       wait up to ``ms`` for a notification on ``uuid`` matching ``data``
               (empty = any). Notifications that arrive during an earlier step
               count, so "write, then expect the reply" never misses a fast reply.
  wait         sleep ``ms`` milliseconds
Expected-value patterns are hex bytes where ``??`` matches any byte,
e.g. ``01 ?? FF``.
"""
from __future__ import annotations

import asyncio
import json
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ..ble import uuids
from ..paths import DATA_DIR
from ..ble.formats import ValueFormat, encode, to_hex

LIBRARY_FILE = DATA_DIR / "macros.json"
FORMAT_VERSION = 1
OPS = ("write", "read", "subscribe", "unsubscribe", "expect", "wait")
DEFAULT_EXPECT_MS = 2000


@dataclass
class Step:
    op: str
    uuid: str = ""
    data: str = ""          # hex: payload for write, expected pattern for read/expect
    response: bool = True   # write: request (True) or command (False)
    ms: int = 0             # wait: duration; expect: timeout

    def __post_init__(self) -> None:
        if self.op not in OPS:
            raise ValueError(f"Unknown macro step '{self.op}'")
        if self.op == "expect" and not self.ms:
            self.ms = DEFAULT_EXPECT_MS

    def describe(self) -> str:
        name = uuids.display_name(self.uuid, "characteristics") if self.uuid else ""
        if self.op == "write":
            kind = "request" if self.response else "command"
            return f"Write {self.data or '(empty)'} to {name} ({kind})"
        if self.op == "read":
            return f"Read {name}" + (f", expect {self.data}" if self.data else "")
        if self.op == "subscribe":
            return f"Subscribe to {name}"
        if self.op == "unsubscribe":
            return f"Unsubscribe from {name}"
        if self.op == "expect":
            what = self.data or "any value"
            return f"Expect notification {what} from {name} within {self.ms} ms"
        return f"Wait {self.ms} ms"

    def validate(self) -> None:
        """Raise ValueError if the step cannot run."""
        if self.op != "wait" and not self.uuid:
            raise ValueError("No characteristic chosen")
        if self.op == "write":
            encode(self.data, ValueFormat.HEX)
        if self.op in ("read", "expect") and self.data:
            parse_pattern(self.data)
        if self.op in ("wait", "expect") and self.ms < 0:
            raise ValueError("Time must not be negative")


@dataclass
class Macro:
    name: str
    steps: list[Step] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"name": self.name, "steps": [_step_dict(s) for s in self.steps]}

    @classmethod
    def from_dict(cls, d: dict) -> "Macro":
        steps = [Step(**{k: v for k, v in s.items() if k in Step.__dataclass_fields__})
                 for s in d.get("steps", [])]
        return cls(str(d.get("name") or "Untitled"), steps)


def _step_dict(s: Step) -> dict:
    # Keep files small and readable: drop fields at their default value.
    d = asdict(s)
    defaults = {"uuid": "", "data": "", "response": True, "ms": 0}
    return {k: v for k, v in d.items() if k == "op" or v != defaults.get(k)}


# -- patterns -----------------------------------------------------------
def parse_pattern(text: str) -> list[int | None]:
    """'01 ?? ff' -> [1, None, 255]. Separators as for hex input."""
    cleaned = text.strip().lower().removeprefix("0x")
    for ch in " -:,":
        cleaned = cleaned.replace(ch, "")
    if len(cleaned) % 2:
        raise ValueError("Expected value needs an even number of hex digits")
    out: list[int | None] = []
    for i in range(0, len(cleaned), 2):
        pair = cleaned[i:i + 2]
        if pair == "??":
            out.append(None)
        else:
            try:
                out.append(int(pair, 16))
            except ValueError:
                raise ValueError(f"'{pair}' is not a hex byte or ??") from None
    return out


def matches(pattern: str, data: bytes) -> bool:
    if not pattern.strip():
        return True
    pat = parse_pattern(pattern)
    return len(pat) == len(data) and all(p is None or p == b for p, b in zip(pat, data))


# -- files --------------------------------------------------------------
def dumps(macros: list[Macro]) -> str:
    return json.dumps({"version": FORMAT_VERSION, "macros": [m.to_dict() for m in macros]},
                      indent=2)


def loads(text: str) -> list[Macro]:
    doc = json.loads(text)
    if isinstance(doc, dict) and "steps" in doc:   # a single exported macro
        items = [doc]
    elif isinstance(doc, dict):
        items = doc.get("macros", [])
    else:
        items = doc
    if not isinstance(items, list) or not all(isinstance(d, dict) for d in items):
        raise ValueError("Not a macro file")
    try:
        return [Macro.from_dict(d) for d in items]
    except (TypeError, AttributeError) as exc:
        raise ValueError(f"Invalid macro: {exc}") from None


class MacroLibrary(QObject):
    """The user's saved macros, shared by every device tab."""

    changed = Signal()

    def __init__(self, path: Path | None = LIBRARY_FILE, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.path = path
        self.macros: list[Macro] = []
        self.load_error: str | None = None
        if path is not None and path.exists():
            try:
                self.macros = loads(path.read_text("utf-8"))
            except (OSError, ValueError, TypeError) as exc:
                self.load_error = f"Could not load {path}: {exc}"

    def save(self) -> None:
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(dumps(self.macros), "utf-8")
            tmp.replace(self.path)
        self.changed.emit()

    def unique_name(self, base: str) -> str:
        names = {m.name for m in self.macros}
        if base not in names:
            return base
        n = 2
        while f"{base} ({n})" in names:
            n += 1
        return f"{base} ({n})"


_library: MacroLibrary | None = None


def library() -> MacroLibrary:
    global _library
    if _library is None:
        _library = MacroLibrary()
    return _library


# -- running ------------------------------------------------------------
class MacroRunner(QObject):
    """Runs a macro against a DeviceSession, one step at a time."""

    step_started = Signal(int)          # step index
    step_finished = Signal(int, bool)   # step index, ok
    finished = Signal(bool, str)        # ok, summary
    running_changed = Signal(bool)

    def __init__(self, session, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self.running = False
        self._task: asyncio.Task | None = None
        self._queues: dict[int, deque[bytes]] = {}
        self._arrived = asyncio.Event()

    def _log(self, level: str, msg: str) -> None:
        self.session.log.emit(level, msg)

    def _on_value(self, handle: int, value: bytes, source: str) -> None:
        if source == "notify":
            self._queues.setdefault(handle, deque(maxlen=256)).append(value)
            self._arrived.set()

    def start(self, macro: Macro, repeat: int = 1, stop_on_error: bool = True) -> None:
        if not self.running:
            self._task = asyncio.ensure_future(self.run(macro, repeat, stop_on_error))

    def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()

    async def run(self, macro: Macro, repeat: int = 1, stop_on_error: bool = True) -> bool:
        if self.running:
            return False
        self.running = True
        self.running_changed.emit(True)
        self._queues.clear()
        self.session.value_updated.connect(self._on_value)
        failures = 0
        ok, summary = True, ""
        try:
            self._log("info", f"Macro '{macro.name}' started"
                      + (f" ({repeat} runs)" if repeat > 1 else ""))
            for run in range(repeat):
                for i, step in enumerate(macro.steps):
                    self.step_started.emit(i)
                    step_ok, why = await self._run_step(step)
                    self.step_finished.emit(i, step_ok)
                    if not step_ok:
                        failures += 1
                        where = f"step {i + 1}" + (f" (run {run + 1})" if repeat > 1 else "")
                        self._log("error", f"Macro '{macro.name}' {where} failed: {why}")
                        if stop_on_error:
                            raise _Abort(f"failed at {where}: {why}")
            summary = "completed" if not failures else f"completed with {failures} failed step(s)"
            ok = not failures
        except _Abort as exc:
            ok, summary = False, str(exc)
        except asyncio.CancelledError:
            ok, summary = False, "stopped"
        finally:
            try:
                self.session.value_updated.disconnect(self._on_value)
            except (RuntimeError, TypeError):
                pass
            self.running = False
            self.running_changed.emit(False)
        self._log("info" if ok else "warning", f"Macro '{macro.name}' {summary}")
        self.finished.emit(ok, summary)
        return ok

    async def _run_step(self, step: Step) -> tuple[bool, str]:
        try:
            step.validate()
        except ValueError as exc:
            return False, str(exc)
        if step.op == "wait":
            await asyncio.sleep(step.ms / 1000)
            return True, ""
        if not self.session.is_connected:
            return False, "not connected"
        ch = self.session.find_characteristic(step.uuid)
        if ch is None:
            return False, f"device has no characteristic {uuids.short_form(step.uuid)}"
        if step.op == "write":
            data = encode(step.data, ValueFormat.HEX)
            response = step.response
            if response and "write" not in ch.properties:
                response = False
            elif not response and "write-without-response" not in ch.properties:
                response = True
            return (await self.session.write(ch.handle, data, response=response)), "write failed"
        if step.op == "read":
            value = await self.session.read(ch.handle)
            if value is None:
                return False, "read failed"
            if not matches(step.data, value):
                return False, f"read {to_hex(value) or '(empty)'}, expected {step.data}"
            return True, ""
        if step.op in ("subscribe", "unsubscribe"):
            enable = step.op == "subscribe"
            if enable:
                self._queues.pop(ch.handle, None)  # only count what arrives from now on
            ok = await self.session.set_notify(ch.handle, enable)
            return ok, "could not change subscription"
        if step.op == "expect":
            return await self._expect(ch.handle, step)
        return False, f"unknown op {step.op}"

    async def _expect(self, handle: int, step: Step) -> tuple[bool, str]:
        if handle not in self.session.notifying:
            return False, "not subscribed (add a Subscribe step first)"
        loop = asyncio.get_running_loop()
        deadline = loop.time() + step.ms / 1000
        seen: list[bytes] = []
        while True:
            q = self._queues.get(handle)
            while q:
                value = q.popleft()
                if matches(step.data, value):
                    return True, ""
                seen.append(value)
            remaining = deadline - loop.time()
            if remaining <= 0:
                if seen:
                    return False, f"got {to_hex(seen[-1]) or '(empty)'}, expected {step.data}"
                return False, f"no notification within {step.ms} ms"
            self._arrived.clear()
            try:
                await asyncio.wait_for(self._arrived.wait(), remaining)
            except asyncio.TimeoutError:
                pass


class _Abort(Exception):
    pass
