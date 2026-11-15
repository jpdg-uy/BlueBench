import asyncio
import json

import pytest
import shiboken6

from bluebench.ble import connection
from bluebench.ble.connection import DeviceSession
from bluebench.core.macros import Macro, MacroLibrary, MacroRunner, Step, dumps, loads, matches, parse_pattern
from bluebench.ui.device_view import DeviceView
from bluebench.ui.macro_view import StepDialog
from fakes import FakeClient, NUS_RX_HANDLE, NUS_TX_HANDLE

HR = "00002a37-0000-1000-8000-00805f9b34fb"
NAME = "00002a00-0000-1000-8000-00805f9b34fb"
NUS_RX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
NUS_TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"


@pytest.fixture
def session(qapp, monkeypatch):
    FakeClient.instances.clear()
    FakeClient.fail_connect = False
    monkeypatch.setattr(connection, "BleakClient", FakeClient)
    s = DeviceSession("AA:BB", "Fake")
    s.logs = []
    s.log.connect(lambda lvl, msg: s.logs.append((lvl, msg)))
    yield s
    shiboken6.delete(s)


@pytest.fixture
def view(session, tmp_path):
    lib = MacroLibrary(tmp_path / "macros.json")
    v = DeviceView(session, library=lib)
    yield v
    shiboken6.delete(v)


async def settle(n=10):
    for _ in range(n):
        await asyncio.sleep(0)


# -- model -------------------------------------------------------------
def test_patterns():
    assert parse_pattern("01 ?? ff") == [1, None, 255]
    assert matches("01-??-FF", b"\x01\x99\xff")
    assert not matches("01 ??", b"\x01")
    assert matches("", b"anything")
    with pytest.raises(ValueError):
        parse_pattern("0g")


def test_json_round_trip_and_compact():
    m = Macro("Echo", [Step("subscribe", NUS_TX), Step("write", NUS_RX, "68 69", response=False),
                       Step("expect", NUS_TX, "68 69"), Step("wait", ms=100)])
    text = dumps([m])
    doc = json.loads(text)
    assert doc["version"] == 1
    assert doc["macros"][0]["steps"][0] == {"op": "subscribe", "uuid": NUS_TX}
    assert doc["macros"][0]["steps"][2]["ms"] == 2000  # expect gets a default timeout
    assert loads(text) == [m]
    assert loads(json.dumps(m.to_dict())) == [m]        # a single exported macro
    with pytest.raises(ValueError):
        Step("explode")


def test_library_save_load(tmp_path):
    p = tmp_path / "sub" / "macros.json"
    lib = MacroLibrary(p)
    lib.macros.append(Macro("A", [Step("read", NAME)]))
    lib.save()
    assert MacroLibrary(p).macros == lib.macros
    assert lib.unique_name("A") == "A (2)"
    p.write_text("{broken")
    assert "Could not load" in MacroLibrary(p).load_error


def test_describe():
    assert Step("write", NUS_RX, "01", response=False).describe() == "Write 01 to NUS RX (command)"
    assert "within 2000 ms" in Step("expect", NUS_TX).describe()


# -- runner ------------------------------------------------------------
async def test_runner_nus_echo(session):
    await session.connect()
    runner = MacroRunner(session)
    done = []
    runner.finished.connect(lambda ok, s: done.append((ok, s)))
    m = Macro("Echo", [Step("subscribe", NUS_TX), Step("write", NUS_RX, "68 69", response=False),
                       Step("expect", NUS_TX, "68 ??", ms=500), Step("read", NAME, "46 61 6b 65 20 48 52 4d")])
    assert await runner.run(m)
    assert done == [(True, "completed")]
    assert FakeClient.instances[-1].writes[0] == (NUS_RX_HANDLE, b"hi", False)


async def test_runner_failures(session):
    await session.connect()
    runner = MacroRunner(session)
    m = Macro("Bad", [Step("read", NAME, "00"), Step("wait", ms=1)])
    assert not await runner.run(m)
    assert any("step 1 failed: read" in msg for _, msg in session.logs)
    # Keep going when asked; count failures.
    statuses = []
    runner.step_finished.connect(lambda i, ok: statuses.append((i, ok)))
    done = []
    runner.finished.connect(lambda ok, s: done.append(s))
    assert not await runner.run(m, repeat=2, stop_on_error=False)
    assert statuses == [(0, False), (1, True)] * 2
    assert done == ["completed with 2 failed step(s)"]


async def test_runner_expect_timeout_and_missing_char(session):
    await session.connect()
    runner = MacroRunner(session)
    assert not await runner.run(Macro("T", [Step("subscribe", NUS_TX), Step("expect", NUS_TX, ms=50)]))
    assert any("no notification within 50 ms" in m for _, m in session.logs)
    assert not await runner.run(Macro("U", [Step("unsubscribe", NUS_TX), Step("expect", NUS_TX, ms=50)]))
    assert any("not subscribed" in m for _, m in session.logs)
    assert not await runner.run(Macro("X", [Step("read", "1234")]))
    assert any("no characteristic 1234" in m for _, m in session.logs)


async def test_runner_stop(session):
    await session.connect()
    runner = MacroRunner(session)
    done = []
    runner.finished.connect(lambda ok, s: done.append((ok, s)))
    runner.start(Macro("Long", [Step("wait", ms=10_000)]))
    await settle()
    assert runner.running
    runner.stop()
    await settle()
    assert not runner.running and done == [(False, "stopped")]


async def test_write_falls_back_to_supported_type(session):
    await session.connect()
    # NUS RX supports both; the HR control point style char 2A00 is write-request only.
    assert await MacroRunner(session).run(Macro("W", [Step("write", NAME, "41", response=False)]))
    assert FakeClient.instances[-1].writes[-1] == (3, b"A", True)


# -- view ----------------------------------------------------------------
async def test_record_and_replay(view, session):
    await session.connect()
    mv = view.macro_view
    mv.set_recording(True)
    assert mv.macro.name.startswith("Recorded")
    await session.set_notify(NUS_TX_HANDLE, True)
    await session.write(NUS_RX_HANDLE, b"hi", response=False)
    await session.read(3)
    mv.set_recording(False)
    await session.read(3)  # not recorded any more
    assert [s.op for s in mv.macro.steps] == ["subscribe", "write", "read"]
    assert mv.macro.steps[1] == Step("write", NUS_RX, "68 69", response=False)
    assert mv.step_list.count() == 3
    # saved to the library file
    assert MacroLibrary(mv.library.path).macros[0].steps == mv.macro.steps

    await session.set_notify(NUS_TX_HANDLE, False)
    mv.run_selected()
    await asyncio.sleep(0.05)
    assert "Completed" in mv.status.text()
    assert mv.step_list.item(0).text().startswith("✓")
    assert mv.macro.steps and len(mv.macro.steps) == 3  # replay is not re-recorded


async def test_edit_steps(view, session, monkeypatch):
    await session.connect()
    mv = view.macro_view
    m = mv.new_macro("Mine")
    mv.add_step("wait", Step("wait", ms=5), edit=False)
    mv.add_step("read", Step("read", NAME), edit=False)
    assert [s.op for s in m.steps] == ["wait", "read"]
    mv.step_list.setCurrentRow(1)
    mv.move_step(-1)
    assert [s.op for s in m.steps] == ["read", "wait"]
    mv.duplicate_macro()
    assert [x.name for x in mv.library.macros] == ["Mine", "Mine (2)"]
    assert mv.macro.name == "Mine (2)"
    mv.remove_step()
    assert len(mv.macro.steps) == 1 and len(m.steps) == 2  # deep copy
    mv.delete_macro(confirm=False)
    assert [x.name for x in mv.library.macros] == ["Mine"]


async def test_export_import(view, session, tmp_path):
    mv = view.macro_view
    mv.new_macro("Share")
    mv.add_step("wait", Step("wait", ms=5), edit=False)
    out = tmp_path / "share.json"
    mv.export_file(out)
    assert mv.import_file(out) == 1
    assert [x.name for x in mv.library.macros] == ["Share", "Share (2)"]
    bad = tmp_path / "bad.json"
    bad.write_text("[1, 2]")
    monkeypatch_warning = []
    import bluebench.ui.macro_view as mvmod
    orig = mvmod.QMessageBox.warning
    mvmod.QMessageBox.warning = staticmethod(lambda *a: monkeypatch_warning.append(a))
    try:
        assert mv.import_file(bad) == 0
    finally:
        mvmod.QMessageBox.warning = orig
    assert monkeypatch_warning


async def test_step_dialog(session, qapp):
    await session.connect()
    dlg = StepDialog(Step("write"), [(NUS_RX, "NUS RX · 6E400002")])
    dlg.data_format.setCurrentText("Text")
    dlg.data_edit.setText("hi")
    step = dlg.build()
    assert step == Step("write", NUS_RX, "68 69")
    dlg.op_combo.setCurrentIndex(dlg.op_combo.findData("expect"))
    assert dlg.ms.value() == 2000
    dlg.data_edit.setText("zz")
    dlg.accept()
    assert "hex" in dlg.error.text()
    dlg.char_combo.setEditText("2a37")
    dlg.data_edit.setText("")
    assert dlg.build().uuid == HR
    shiboken6.delete(dlg)


async def test_run_disabled_when_disconnected(view):
    mv = view.macro_view
    mv.new_macro("X")
    mv.add_step("wait", Step("wait", ms=5), edit=False)
    assert not mv.run_btn.isEnabled()
    await view.session.connect()
    assert mv.run_btn.isEnabled()
    assert view.pages.tabText(1) == "UART" and view.pages.tabText(2) == "Macros"
