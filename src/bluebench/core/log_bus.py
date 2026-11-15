"""Central log: every BLE operation from the scanner and all device sessions."""
from __future__ import annotations

import csv
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal

LEVELS = ("debug", "info", "warning", "error")
MAX_ENTRIES = 20_000


def level_rank(level: str) -> int:
    try:
        return LEVELS.index(level)
    except ValueError:
        return LEVELS.index("info")


@dataclass(frozen=True)
class LogEntry:
    timestamp: float  # time.time()
    level: str
    source: str
    message: str

    @property
    def time_text(self) -> str:
        t = time.localtime(self.timestamp)
        return time.strftime("%H:%M:%S", t) + f".{int(self.timestamp % 1 * 1000):03d}"

    @property
    def date_time_text(self) -> str:
        return time.strftime("%Y-%m-%d ", time.localtime(self.timestamp)) + self.time_text

    def as_line(self) -> str:
        return f"{self.date_time_text}  {self.level.upper():<7}  {self.source}: {self.message}"


class LogBus(QObject):
    entry_added = Signal(object)  # LogEntry
    cleared = Signal()

    def __init__(self, max_entries: int = MAX_ENTRIES, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.entries: deque[LogEntry] = deque(maxlen=max_entries)

    @property
    def max_entries(self) -> int:
        return self.entries.maxlen or MAX_ENTRIES

    def log(self, level: str, source: str, message: str) -> LogEntry:
        entry = LogEntry(time.time(), level if level in LEVELS else "info", source, message)
        self.entries.append(entry)
        self.entry_added.emit(entry)
        return entry

    def clear(self) -> None:
        self.entries.clear()
        self.cleared.emit()

    def sources(self) -> list[str]:
        return sorted({e.source for e in self.entries})


def export(entries: list[LogEntry], path: str | Path) -> None:
    """Write entries as CSV (by .csv extension) or plain text otherwise."""
    path = Path(path)
    if path.suffix.lower() == ".csv":
        with path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["timestamp", "level", "source", "message"])
            for e in entries:
                w.writerow([e.date_time_text, e.level, e.source, e.message])
    else:
        path.write_text("".join(e.as_line() + "\n" for e in entries), encoding="utf-8")
