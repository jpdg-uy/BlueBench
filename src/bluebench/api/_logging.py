"""Bridges the core's Qt ``log(level, message)`` signals to Python ``logging``."""
from __future__ import annotations

import logging

logger = logging.getLogger("bluebench")

LEVELS = {"debug": logging.DEBUG, "info": logging.INFO,
          "warning": logging.WARNING, "error": logging.ERROR}


class LogTap:
    """Forwards a log signal to the ``bluebench`` logger and remembers the last
    error, which the API uses as the message of the exception it raises (the core
    reports failures by logging them and returning None/False)."""

    def __init__(self, signal, source: str) -> None:
        self.source = source
        self.last_error: str | None = None
        self.first_error: str | None = None   # since the last reset()
        self._signal = signal
        signal.connect(self._on_log)

    def _on_log(self, level: str, message: str) -> None:
        logger.log(LEVELS.get(level, logging.INFO), "%s: %s", self.source, message)
        if level in ("error", "warning"):
            self.last_error = message
            if self.first_error is None:
                self.first_error = message

    def reset(self) -> None:
        self.last_error = self.first_error = None

    def error(self, fallback: str) -> str:
        return self.last_error or fallback

    def close(self) -> None:
        try:
            self._signal.disconnect(self._on_log)
        except (RuntimeError, TypeError):
            pass
