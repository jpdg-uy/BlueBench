import asyncio
import json
import zipfile

import pytest
import shiboken6

from bluebench.ble import connection
from bluebench.ble.connection import DeviceSession
from bluebench.tools.dfu import package as pkg, secure_dfu
from bluebench.tools.dfu.secure_dfu import SecureDfu, bootloader_address
from bluebench.ui.device_view import DeviceView
from fake_dfu import FakeApp, FakeBootloader
from fakes import FakeClient

APP_ADDR = "AA:BB:CC:DD:EE:FF"
BOOT_ADDR = "AA:BB:CC:DD:EF:00"
FW = bytes(range(256)) * 13 + b"tail"          # 3332 bytes: 4 objects of 1024
INIT = b"\x12\x34signed-init-packet"


def make_zip(path, images=("application",), fw=FW):
    manifest = {}
    with zipfile.ZipFile(path, "w") as z:
        for kind in images:
            z.writestr(f"{kind}.bin", fw)
            z.writestr(f"{kind}.dat", INIT if kind == "application" else INIT + kind.encode())
            manifest[kind] = {"bin_file": f"{kind}.bin", "dat_file": f"{kind}.dat"}
        z.writestr("manifest.json", json.dumps({"manifest": manifest}))
    return path


@pytest.fixture
def world(qapp, monkeypatch):
    FakeClient.instances.clear()
    FakeClient.fail_connect = False
    FakeBootloader.flash = {"expected": len(FW)}
    FakeBootloader.corrupt_once_at = None
    FakeBootloader.reject_init = False
    FakeApp.named.clear()

    def factory(device, disconnected_callback=None, timeout=10.0):
        addr = device if isinstance(device, str) else device.address
        cls = FakeBootloader if addr == BOOT_ADDR else FakeApp
        return cls(device, disconnected_callback, timeout)
    monkeypatch.setattr(connection, "BleakClient", factory)
    looked_for = []

    async def find(address, name, timeout=1.0):
        looked_for.append((address, name))
        return address
    monkeypatch.setattr(secure_dfu, "find_device", find)
    return looked_for


async def run_dfu(session, package, **kw):
    dfu = SecureDfu(session, package, **kw)
    events = {"stage": [], "progress": [], "log": [], "done": []}
    dfu.stage.connect(events["stage"].append)
    dfu.progress.connect(lambda d, t: events["progress"].append((d, t)))
    dfu.log.connect(lambda lvl, m: events["log"].append((lvl, m)))
    dfu.finished.connect(lambda ok, s: events["done"].append((ok, s)))
    ok = await dfu.run()
    return ok, events


# -- package ---------------------------------------------------------------
def test_package_load(tmp_path):
    p = pkg.load(make_zip(tmp_path / "a.zip", ("application", "softdevice_bootloader")))
    assert [i.kind for i in p.images] == ["softdevice_bootloader", "application"]
    assert p.total_size == 2 * len(FW) and p.images[1].init_packet == INIT
    assert "Application (3.3 KB)" in p.describe()


def test_package_errors(tmp_path):
    bad = tmp_path / "x.zip"
    bad.write_bytes(b"nope")
    with pytest.raises(pkg.PackageError, match="zip"):
        pkg.load(bad)
    with zipfile.ZipFile(tmp_path / "ncs.zip", "w") as z:
        z.writestr("manifest.json", json.dumps({"format-version": 0, "files": []}))
    with pytest.raises(pkg.PackageError, match="MCUboot"):
        pkg.load(tmp_path / "ncs.zip")
    with zipfile.ZipFile(tmp_path / "miss.zip", "w") as z:
        z.writestr("manifest.json", json.dumps({"manifest": {"application": {
            "bin_file": "a.bin", "dat_file": "a.dat"}}}))
    with pytest.raises(pkg.PackageError, match="missing from the zip"):
        pkg.load(tmp_path / "miss.zip")


def test_bootloader_address():
    assert bootloader_address(APP_ADDR) == BOOT_ADDR
    assert bootloader_address("FF:FF:FF:FF:FF:FF") == "00:00:00:00:00:00"


# -- protocol ---------------------------------------------------------------
async def test_buttonless_dfu_end_to_end(world, tmp_path):
    s = DeviceSession(APP_ADDR, "App")
    await s.connect()
    ok, ev = await run_dfu(s, pkg.load(make_zip(tmp_path / "a.zip")))
    assert ok, ev["log"]
    assert FakeBootloader.flash["activated"] == [FW]
    assert FakeApp.named and world == [(BOOT_ADDR, FakeApp.named[0])]
    assert ev["progress"][-1] == (len(FW), len(FW))
    assert ev["done"][0][1].startswith("DFU complete")
    assert not s.is_connected
    assert not any(lvl in ("warning", "error") for lvl, _ in ev["log"]), ev["log"]
    shiboken6.delete(s)


@pytest.mark.parametrize("prn", [0, 3])
async def test_direct_bootloader_with_prn_and_crc_retry(world, tmp_path, prn):
    FakeBootloader.corrupt_once_at = 1500   # second object gets a bad byte once
    s = DeviceSession(BOOT_ADDR, "DfuTarg")
    await s.connect()
    ok, ev = await run_dfu(s, pkg.load(make_zip(tmp_path / "a.zip")), prn=prn)
    assert ok, ev["log"]
    assert FakeBootloader.flash["activated"] == [FW]
    assert any("CRC mismatch" in m for _, m in ev["log"])
    assert world == []  # already in bootloader: no scan
    shiboken6.delete(s)


async def test_resume_after_interruption(world, tmp_path):
    # A previous attempt got 2 full objects + half of the third in.
    FakeBootloader.flash.update(cmd=INIT, validated=INIT, data=FW[:2500], executed=2048)
    s = DeviceSession(BOOT_ADDR, "DfuTarg")
    await s.connect()
    ok, ev = await run_dfu(s, pkg.load(make_zip(tmp_path / "a.zip")))
    assert ok, ev["log"]
    assert any("Resuming firmware transfer at 2048" in m for _, m in ev["log"])
    assert ev["progress"][0][0] == 2048
    assert FakeBootloader.flash["activated"] == [FW]
    shiboken6.delete(s)


async def test_init_rejected(world, tmp_path):
    FakeBootloader.reject_init = True
    s = DeviceSession(BOOT_ADDR, "DfuTarg")
    await s.connect()
    ok, ev = await run_dfu(s, pkg.load(make_zip(tmp_path / "a.zip")))
    assert not ok
    assert "signature verification failed" in ev["done"][0][1]
    shiboken6.delete(s)


async def test_bootloader_not_found(world, tmp_path, monkeypatch):
    async def nothing(address, name, timeout=1.0):
        return None
    monkeypatch.setattr(secure_dfu, "find_device", nothing)
    s = DeviceSession(APP_ADDR, "App")
    await s.connect()
    ok, ev = await run_dfu(s, pkg.load(make_zip(tmp_path / "a.zip")))
    assert not ok and "bootloader not found" in ev["done"][0][1]
    shiboken6.delete(s)


async def test_multi_image_reconnects(world, tmp_path):
    FakeBootloader.flash["expected"] = len(FW)
    s = DeviceSession(APP_ADDR, "App")
    await s.connect()
    p = pkg.load(make_zip(tmp_path / "a.zip", ("softdevice", "application")))
    ok, ev = await run_dfu(s, p)
    assert ok, ev["log"]
    assert FakeBootloader.flash["activated"] == [FW, FW]
    assert not any("Resuming" in m for _, m in ev["log"])  # second image sent in full
    assert len(world) == 2  # scanned for the bootloader before each image
    assert ev["progress"][-1] == (2 * len(FW), 2 * len(FW))
    shiboken6.delete(s)


async def test_abort(world, tmp_path):
    s = DeviceSession(BOOT_ADDR, "DfuTarg")
    await s.connect()
    dfu = SecureDfu(s, pkg.load(make_zip(tmp_path / "a.zip")))
    done = []
    dfu.finished.connect(lambda ok, m: done.append((ok, m)))
    dfu.progress.connect(lambda d, t: d > 500 and dfu.abort())
    dfu.start()
    for _ in range(200):
        await asyncio.sleep(0.005)
        if done:
            break
    assert done and not done[0][0] and "aborted" in done[0][1]
    assert not FakeBootloader.flash["activated"]
    assert not dfu.running
    shiboken6.delete(s)


# -- UI --------------------------------------------------------------------
async def test_dfu_button_and_dialog(world, tmp_path):
    v = DeviceView(DeviceSession(APP_ADDR, "App"))
    await v.session.connect()
    assert not v.dfu_btn.isHidden()
    dlg = v.open_dfu()
    assert not dlg.load_package(tmp_path / "missing.zip") and not dlg.start_btn.isEnabled()
    assert dlg.load_package(make_zip(tmp_path / "a.zip")) and dlg.start_btn.isEnabled()
    dlg.start()
    assert not dlg.close_btn.isEnabled()
    for _ in range(300):
        await asyncio.sleep(0.005)
        if not dlg.dfu.running:
            break
    assert dlg.bar.value() == 100
    assert "DFU complete" in dlg.stage.text()
    assert dlg.close_btn.isEnabled()
    dlg.close()
    shiboken6.delete(v)


async def test_no_dfu_button_without_service(qapp, monkeypatch):
    FakeClient.instances.clear()
    monkeypatch.setattr(connection, "BleakClient", FakeClient)
    v = DeviceView(DeviceSession("AA", "plain"))
    await v.session.connect()
    assert v.dfu_btn.isHidden()
    shiboken6.delete(v)
