"""Small shims over Qt API differences between supported PySide6 versions."""
from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager

from PySide6.QtCore import QSortFilterProxyModel


@contextmanager
def filter_change(proxy: QSortFilterProxyModel) -> Iterator[None]:
    """Wrap a change to a proxy's filter criteria.

    Qt 6.10 replaced invalidateFilter() with beginFilterChange()/endFilterChange().
    """
    if hasattr(proxy, "endFilterChange"):  # 6.9 has begin… but not end…
        proxy.beginFilterChange()
        try:
            yield
        finally:
            proxy.endFilterChange()
    else:
        yield
        proxy.invalidateFilter()


def _pyside_version() -> tuple[int, ...]:
    import PySide6
    return tuple(int(p) for p in PySide6.__version__.split(".")[:2] if p.isdigit())


# PySide6 6.12 on Linux aborts ("free(): invalid pointer") during interpreter
# finalisation once any Qt signal has been emitted; reproduced without project
# code, 6.11.3 and earlier are clean. It is the only release with wheels for
# Python 3.15, so instead of excluding it we skip finalisation after a clean
# shutdown. On Windows a normal exit is clean, and os._exit is what crashes
# (access violation in DLL detach), so the workaround is Linux-only.
# Re-check when a fixed 6.12.x ships.
NEEDS_HARD_EXIT = sys.platform.startswith("linux") and _pyside_version() >= (6, 12)


def hard_exit(code: int) -> None:
    """Exit without interpreter finalisation. Call only once everything is shut down."""
    import os
    import sys

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    os._exit(code)
