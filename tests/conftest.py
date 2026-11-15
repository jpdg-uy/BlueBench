import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


# See bluebench.ui.qt_compat.NEEDS_HARD_EXIT: on affected PySide6 versions
# the interpreter aborts after pytest finishes, turning a green run into exit
# code 134. Exit with pytest's real status once the summary has been printed.
_exit_status = 0


def pytest_sessionfinish(session, exitstatus):
    global _exit_status
    _exit_status = int(exitstatus)


@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config):
    from bluebench.ui.qt_compat import NEEDS_HARD_EXIT, hard_exit
    if NEEDS_HARD_EXIT:
        hard_exit(_exit_status)


@pytest.fixture(autouse=True)
def _isolated_macro_library(monkeypatch):
    """Never read or write the real ~/.bluebench/macros.json from tests."""
    from bluebench.core import macros
    monkeypatch.setattr(macros, "_library", macros.MacroLibrary(path=None))
