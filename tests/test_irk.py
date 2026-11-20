"""Identity Resolving Keys: AES, ah(), the key ring, the scanner and the CLI."""
import json

import pytest

from bluebench.ble import irk, registry

IRK = bytes.fromhex("ec0234a357c8ad05341010a60a397d9b")     # the spec's sample IRK


def test_aes_and_ah_spec_vectors():
    key = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
    assert irk.aes128_encrypt(key, bytes.fromhex("00112233445566778899aabbccddeeff")).hex() == \
        "69c4e0d86a7b0430d8cdb78070b4c55a"                       # FIPS-197 C.1
    assert irk.ah(IRK, bytes.fromhex("708194")).hex() == "0dfbaa"  # Core spec sample data


def test_resolves_rpas_in_either_byte_order():
    rpa = "70:81:94:0D:FB:AA"
    assert irk.is_rpa(rpa) and irk.resolves(IRK, rpa)
    assert irk.IdentityKey("x", IRK[::-1]).matches(rpa)          # written LSB first
    assert not irk.resolves(IRK, "70:81:94:0D:FB:AB")
    assert not irk.resolves(IRK, "C0:81:94:0D:FB:AA")             # static address
    assert irk.resolves(IRK, irk.make_rpa(IRK, b"\x12\x34\x56"))


def test_parse_irk():
    assert irk.parse_irk("0x" + IRK.hex()) == IRK
    assert irk.parse_irk(IRK.hex(":").upper()) == IRK
    with pytest.raises(ValueError, match="16 bytes"):
        irk.parse_irk("0102")
    with pytest.raises(ValueError, match="hex"):
        irk.parse_irk("zz" * 16)


def test_key_ring_file_and_cache(tmp_path):
    path = tmp_path / "irks.json"
    ring = irk.KeyRing(path=path)
    changes = []
    ring.listeners.append(lambda: changes.append(1))
    ring.add("Board 1", IRK.hex())
    ring.add("Board 2", "00" * 16)
    ring.save()
    assert json.loads(path.read_text())[0] == {"label": "Board 1", "irk": IRK.hex().upper()}
    again = irk.KeyRing.load(path)
    assert [k.label for k in again.keys] == ["Board 1", "Board 2"]
    assert again.resolve("70:81:94:0D:FB:AA").label == "Board 1"
    assert again.resolve("C0:00:00:00:00:01") is None
    assert again.remove("Board 1") and not again.remove("nope")
    assert again.resolve("70:81:94:0D:FB:AA") is None              # cache cleared
    assert changes == [1, 1]
    with pytest.raises(ValueError, match="label"):
        ring.add("  ", IRK)
    path.write_text("not json")
    assert irk.KeyRing.load(path).keys == []                        # ignored, not fatal


def test_filter_field_registered():
    assert "irk" in registry.filter_fields()


def test_cli_irk_commands(capsys, monkeypatch, tmp_path):
    from bluebench import cli
    monkeypatch.setattr(irk, "_default", irk.KeyRing(path=tmp_path / "irks.json"))
    assert cli.main(["irk", "add", "Board 1", IRK.hex()]) == 0
    assert cli.main(["--json", "irk", "list"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert json.loads(out[-1]) == [{"label": "Board 1", "irk": IRK.hex()}]
    assert cli.main(["irk", "resolve", "70:81:94:0D:FB:AA"]) == 0
    assert "Board 1" in capsys.readouterr().out
    assert cli.main(["irk", "resolve", irk.make_rpa(b"\x01" * 16, b"\x00\x00\x01")]) == 3
    assert cli.main(["irk", "resolve", "C0:00:00:00:00:01"]) == 2
    assert cli.main(["irk", "remove", "Board 1"]) == 0
    assert cli.main(["irk", "remove", "Board 1"]) == 1
    assert json.loads((tmp_path / "irks.json").read_text()) == []


def test_dialog_adds_checks_and_removes(qapp, tmp_path):
    from bluebench.ui.irk_dialog import IrkDialog
    ring = irk.KeyRing(path=tmp_path / "irks.json")
    dlg = IrkDialog(ring)
    dlg.label_edit.setText("Board 1")
    dlg.irk_edit.setText("12")
    dlg.add()
    assert dlg.status.text().startswith("⚠") and dlg.table.rowCount() == 0
    dlg.irk_edit.setText(IRK.hex())
    dlg.add()
    assert dlg.table.rowCount() == 1 and (tmp_path / "irks.json").exists()
    dlg.check_edit.setText("70:81:94:0D:FB:AA")
    assert dlg.check_result.text() == "✓ Board 1"
    dlg.check_edit.setText("C0:00:00:00:00:01")
    assert "not a resolvable" in dlg.check_result.text()
    dlg.table.selectRow(0)
    dlg.remove_selected()
    assert ring.keys == [] and dlg.table.rowCount() == 0
    dlg.deleteLater()


def test_scan_results_and_filter_carry_the_label(qapp):
    from types import SimpleNamespace
    from bluebench.api.scan import ScanResult, make_filter, _matches
    from bluebench.ble.adv_parser import parse_advertisement
    adv = parse_advertisement("x", {}, {}, [], None)
    adv.identity = ("irk", "Board 1")
    e = SimpleNamespace(address="70:81:94:0D:FB:AA", name="x", rssi=-50, adv=adv, adv_count=3,
                        device=None, resolved_by="Board 1", addresses={}, identity_address=None)
    assert ScanResult.from_entry(e).to_dict()["resolved_by"] == "Board 1"
    assert _matches(make_filter(irk="board 1"), e) and not _matches(make_filter(irk="Board 2"), e)


def test_scanner_view_marks_resolved_rows(qapp):
    from types import SimpleNamespace
    from bluebench.ui.scanner_view import address_text, address_tip
    e = SimpleNamespace(address="70:81:94:0D:FB:AA", address_kind="rpa", resolved_by="Board 1",
                        addresses={"4A:00:00:00:00:01": 1.0, "70:81:94:0D:FB:AA": 2.0},
                        first_heard={"4A:00:00:00:00:01": 1.0}, last_seen=2.0, joined="identity")
    assert address_text(e) == "70:81:94:0D:FB:AA  RPA  ⚿  ↻2"
    tip = address_tip(e)
    assert "Resolved with the IRK “Board 1”" in tip and "resolve with the IRK" in tip
