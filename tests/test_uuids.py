from bluebench.ble import uuids


def test_normalize_short_forms():
    full = "0000180d-0000-1000-8000-00805f9b34fb"
    assert uuids.normalize("180D") == full
    assert uuids.normalize("0x180d") == full
    assert uuids.normalize(0x180D) == full
    assert uuids.normalize(full.upper()) == full
    assert uuids.normalize("0000180d00001000800000805f9b34fb") == full


def test_short_form():
    assert uuids.short_form("0000180d-0000-1000-8000-00805f9b34fb") == "180D"
    nus = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
    assert uuids.short_form(nus) == nus.upper()


def test_names():
    assert uuids.name_for("180d", "services") == "Heart Rate"
    assert uuids.name_for("6E400001-B5A3-F393-E0A9-E50E24DCCA9E", "services") == "Nordic UART Service"
    assert uuids.name_for("2902", "descriptors") == "Client Characteristic Configuration"
    assert uuids.display_name("1234", "characteristics") == "Unknown Characteristic"
    assert uuids.company_name(0x0059) == "Nordic Semiconductor"
    assert uuids.company_name(0xFFFE) == "Unknown (0xFFFE)"


def test_legacy_data_folder_is_copied_once(tmp_path):
    from bluebench.paths import migrate_legacy
    old, new = tmp_path / ".nrfconnecter", tmp_path / ".bluebench"
    assert not migrate_legacy(new, old)          # nothing to migrate
    old.mkdir()
    (old / "macros.json").write_text("{}", "utf-8")
    assert migrate_legacy(new, old)
    assert (new / "macros.json").read_text("utf-8") == "{}" and (old / "macros.json").exists()
    (old / "macros.json").write_text("changed", "utf-8")
    assert not migrate_legacy(new, old)          # never overwrites
    assert (new / "macros.json").read_text("utf-8") == "{}"
