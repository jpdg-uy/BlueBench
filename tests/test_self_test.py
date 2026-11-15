import json
from types import SimpleNamespace

import bleak

from bluebench.__main__ import self_test


def test_self_test_reports_devices(tmp_path, monkeypatch):
    async def discover(timeout, return_adv):
        adv = SimpleNamespace(local_name="Thingy", rssi=-60)
        return {"AA:BB": (None, adv)}
    monkeypatch.setattr(bleak.BleakScanner, "discover", staticmethod(discover))
    out = tmp_path / "r.txt"
    assert self_test(str(out), seconds=0) == 0
    r = json.loads(out.read_text())
    assert r["ok"] and r["devices_found"] == 1 and r["sample"] == ["AA:BB Thingy -60 dBm"]


def test_self_test_reports_failure(tmp_path, monkeypatch):
    async def discover(timeout, return_adv):
        raise OSError("Bluetooth adapter not found")
    monkeypatch.setattr(bleak.BleakScanner, "discover", staticmethod(discover))
    out = tmp_path / "r.txt"
    assert self_test(str(out), seconds=0) == 1
    r = json.loads(out.read_text())
    assert not r["ok"] and r["error"] == "OSError: Bluetooth adapter not found"
    # Bundling checks are reported even without a radio (the release smoke test relies on it).
    assert r["pyserial"] and isinstance(r["serial_ports"], list) and r["frozen"] is False
