import pytest

from bluebench.ble import adv_filter
from bluebench.ble.adv_filter import DeviceFilter

ADV = dict(address="C4:3A:00:00:00:01", name="Sensor Demo", rssi=-60,
           manufacturer_data={0x0059: b"\xaa\xbb"}, service_uuids=["180d"])


def test_hex_query():
    assert adv_filter.hex_query(" 0x59-00 ") == "5900"
    assert adv_filter.hex_query("Alpha") is None and adv_filter.hex_query("abc") is None
    assert adv_filter.hex_query("ab") == "ab"


@pytest.mark.parametrize("text, ok", [
    ("5900aabb", True), ("0059aabb", True),     # plain bytes, both orders
    ("0059??bb", True), ("5900 ?? bb", True),   # skip one byte
    ("0059?abb", True), ("0059?bbb", False),    # one hex digit
    ("*bb", True), ("*aa", True), ("*cc", False),
    ("0059*bb", True), ("0059*aa", True), ("00*bb", True),
    ("*59", True),                              # in the on-air form
    ("*9a", False),                             # never across a byte boundary
    ("??", True), ("0059??bbcc", False),
])
def test_manufacturer_wildcards(text, ok):
    assert adv_filter.manufacturer_matches({0x0059: b"\xaa\xbb"}, text) is ok


@pytest.mark.parametrize("text", ["", "*", "abc", "0059?", "00*5", "Sensor", "0x"])
def test_byte_pattern_rejects(text):
    assert adv_filter.byte_pattern(text) is None


def test_manufacturer_matches_both_byte_orders():
    data = {0x0059: b"\xaa"}
    assert adv_filter.manufacturer_matches(data, "5900aa")   # on air
    assert adv_filter.manufacturer_matches(data, "0059aa")   # as usually written
    assert not adv_filter.manufacturer_matches(data, "5900ab")
    assert not adv_filter.manufacturer_matches({}, "5900")


@pytest.mark.parametrize("flt, ok", [
    (DeviceFilter(), True),
    (DeviceFilter(name="sensor"), True),
    (DeviceFilter(name="other"), False),
    (DeviceFilter(address="c4:3a:00:00:00:01"), True),
    (DeviceFilter(address="C4:3A:00:00:00:02"), False),
    (DeviceFilter(mfr="5900"), True),
    (DeviceFilter(mfr="0x59 00 aa"), True),
    (DeviceFilter(mfr="5901"), False),
    (DeviceFilter(mfr="0059 ?? bb"), True),
    (DeviceFilter(mfr="*bb"), True),
    (DeviceFilter(service="0000180d-0000-1000-8000-00805f9b34fb"), True),
    (DeviceFilter(service="180f"), False),
    (DeviceFilter(min_rssi=-70), True),
    (DeviceFilter(min_rssi=-50), False),
    (DeviceFilter(name="Sensor", mfr="5900", min_rssi=-70), True),
])
def test_device_filter(flt, ok):
    assert flt.matches(**ADV) is ok


def test_device_filter_rejects_bad_hex():
    with pytest.raises(ValueError, match="hex"):
        DeviceFilter(mfr="Sensor")
    assert DeviceFilter().is_empty and not DeviceFilter(min_rssi=-80).is_empty
