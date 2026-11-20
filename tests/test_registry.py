"""Extension points: advertising decoders and extra names."""
import pytest

from bluebench.ble import registry, uuids
from bluebench.ble.adv_parser import AdvField, parse_advertisement
from bluebench.ble.registry import Decoded


@pytest.fixture
def clean():
    restore = registry._reset_for_tests()
    yield
    restore()
    uuids.reload()


def test_decoder_adds_fields_label_and_identity(clean):
    def demo(adv):
        p = adv.manufacturer_data.get(0x0059)
        if not p:
            return None
        return Decoded([AdvField("Demo", "serial 7")], label="Demo · 7", identity=("demo", 7))
    registry.register_adv_decoder(demo)
    registry.register_adv_decoder(demo)  # registering twice is harmless
    adv = parse_advertisement(None, {0x0059: b"\x07"}, {}, [], None)
    assert adv.beacon == "Demo · 7" and adv.identity == ("demo", 7)
    assert [f.label for f in adv.fields][-1] == "Demo" and len(adv.fields) == 2
    other = parse_advertisement(None, {0x004C: b"\x00"}, {}, [], None)
    assert other.identity is None and other.beacon is None


def test_broken_decoder_is_skipped(clean):
    def broken(adv):
        raise ValueError("boom")
    registry.register_adv_decoder(broken)
    assert parse_advertisement("x", {}, {}, [], None).local_name == "x"


def test_extra_names(clean):
    assert uuids.display_name("fd00", "services") == "Unknown Service"
    registry.register_names({"services": {"fd00": "Demo Service"}})
    assert uuids.display_name("fd00", "services") == "Demo Service"


def test_extensions_are_imported_once(monkeypatch):
    import sys, types
    from bluebench import extensions
    mod = types.ModuleType("demo_ext")
    mod.loaded = 0
    calls = []
    monkeypatch.setitem(sys.modules, "demo_ext", mod)
    monkeypatch.setattr(extensions, "MODULES", ["demo_ext", "missing_ext_xyz"])
    monkeypatch.setattr(registry, "_loaded", False)
    monkeypatch.setattr(registry.importlib, "import_module",
                        lambda n: calls.append(n) or sys.modules[n])
    registry.load_extensions()
    registry.load_extensions()
    assert calls == ["demo_ext", "missing_ext_xyz"]
