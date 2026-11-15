"""Entry point: ``python -m bluebench``."""
from __future__ import annotations

import asyncio
import sys


def install_error_handlers(loop, log_bus) -> None:
    """Send unhandled errors to the log console.

    The packaged .exe has no console, so without this an exception in a
    Qt slot or a background task would disappear without a trace.
    """
    import traceback

    def report(summary: str, exc: BaseException | None) -> None:
        detail = ("".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
                  if exc is not None else "")
        try:
            log_bus.log("error", "App", summary)
            if detail:
                log_bus.log("debug", "App", detail.rstrip())
        except RuntimeError:
            # The log bus is gone (e.g. during teardown); stderr is all that is left.
            if sys.stderr is not None:
                print(summary, detail, sep="\n", file=sys.stderr)

    def on_async_error(_loop, context: dict) -> None:
        exc = context.get("exception")
        msg = context.get("message") or "Unhandled error in background task"
        report(f"{msg}: {exc!r}" if exc else msg, exc)

    def on_exception(exc_type, exc, tb) -> None:
        report(f"Unexpected error: {exc!r}", exc)

    loop.set_exception_handler(on_async_error)
    sys.excepthook = on_exception


def self_test(report_path: str | None, seconds: float = 4.0) -> int:
    """Headless check that the Bluetooth stack works: scan briefly, report.

    ``BlueBench.exe --self-test [report.txt]``. The windowed .exe has no
    console, so the result is also written to a file (default:
    bluebench-selftest.txt next to the executable).
    """
    import json
    import platform
    from pathlib import Path

    from . import __version__

    result: dict = {"version": __version__, "python": platform.python_version(),
                    "frozen": bool(getattr(sys, "frozen", False))}

    async def scan() -> None:
        from bleak import BleakScanner
        devices = await BleakScanner.discover(timeout=seconds, return_adv=True)
        result["devices_found"] = len(devices)
        result["sample"] = sorted(
            f"{addr} {adv.local_name or '-'} {adv.rssi} dBm" for addr, (_, adv) in devices.items()
        )[:10]

    # nRF Sniffer support (Traffic tab), checked first so it is reported even
    # when there is no Bluetooth radio: serial ports, ★ = Nordic/SEGGER USB ids.
    try:
        import serial
        from .sniffer.nrf_sniffer import list_ports
        result["pyserial"] = serial.__version__
        result["serial_ports"] = [p.label for p in list_ports()]
    except Exception as exc:
        result["serial_ports"] = f"unavailable: {type(exc).__name__}: {exc}"

    try:
        from importlib.metadata import PackageNotFoundError, version
        try:
            result["bleak"] = version("bleak")
        except PackageNotFoundError:
            result["bleak"] = "?"
        asyncio.run(scan())
        result["ok"] = True
    except Exception as exc:  # report, don't crash
        result["ok"] = False
        result["error"] = f"{type(exc).__name__}: {exc}"

    if report_path is None:
        base = Path(sys.executable).parent if result["frozen"] else Path.cwd()
        report_path = str(base / "bluebench-selftest.txt")
    text = json.dumps(result, indent=2)
    Path(report_path).write_text(text + "\n", encoding="utf-8")
    if sys.stdout is not None:
        print(text)
    return 0 if result["ok"] else 1


def main() -> int:
    if "--self-test" in sys.argv:
        i = sys.argv.index("--self-test")
        path = sys.argv[i + 1] if len(sys.argv) > i + 1 else None
        return self_test(path)

    from PySide6.QtWidgets import QApplication
    import qasync

    from .paths import migrate_legacy
    migrate_legacy()   # bring macros / custom names over from nRFConnecter

    from .ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("BlueBench")
    # Closing the window must not stop Qt straight away: MainWindow.closeEvent
    # first disconnects devices on the asyncio loop, then calls app.quit().
    app.setQuitOnLastWindowClosed(False)
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    window = MainWindow()
    install_error_handlers(loop, window.log_bus)
    window.show()

    with loop:
        loop.run_forever()  # returns when app.quit() ends the Qt event loop

    from .ui.qt_compat import NEEDS_HARD_EXIT, hard_exit
    if NEEDS_HARD_EXIT:
        hard_exit(0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
