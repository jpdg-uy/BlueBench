"""The public Python API, headless (no QApplication), against fake bleak objects."""
import asyncio
import json
import logging
from contextlib import aclosing

import pytest

from bluebench import api
from bluebench.ble import connection, pairing, scanner
from bluebench.core.macros import Macro, Step, dumps
from fakes import NUS_TX_HANDLE, FakeClient, FakeScanner, advert

SENSOR = "C4:3A:00:00:00:01"
HRM = "C4:3A:00:00:00:02"


@pytest.fixture(autouse=True)
def fake_ble(monkeypatch):
    FakeClient.instances.clear()
    FakeClient.unpaired.clear()
    FakeClient.fail_connect = FakeClient.fail_pair = False
    FakeScanner.fail = None
    FakeScanner.adverts = [
        advert(HRM, "Fake HRM", -70, services=["180d"]),
        advert(SENSOR, None, -50, mfr={0x0059: b"\xaa\xbb"}),
        # The scan response adds the name; bleak reports it merged with the advertisement.
        advert(SENSOR, "Sensor Demo", -52, mfr={0x0059: b"\xaa\xbb"}),
    ]
    monkeypatch.setattr(scanner, "BleakScanner", FakeScanner)
    monkeypatch.setattr(connection, "BleakClient", FakeClient)
    monkeypatch.setattr(pairing, "BleakClient", FakeClient)
    monkeypatch.setattr(pairing, "_is_winrt", lambda: False)


def client() -> FakeClient:
    return FakeClient.instances[-1]


# -- scanning -----------------------------------------------------------------
async def test_scan_merges_packets_and_sorts_by_rssi():
    found = await api.scan(timeout=0.1)
    assert [r.address for r in found] == [SENSOR, HRM]
    sensor = found[0]
    assert sensor.name == "Sensor Demo" and sensor.packets == 2 and sensor.manufacturer_data == {0x0059: b"\xaa\xbb"}
    d = sensor.to_dict()
    assert d["manufacturer_data"] == [{"company_id": "0x0059", "company": d["manufacturer_data"][0]["company"],
                                       "data": "aabb", "raw": "5900aabb"}]
    json.dumps(d)  # JSON-ready


@pytest.mark.parametrize("criteria, expected", [
    ({"mfr": "5900"}, [SENSOR]), ({"mfr": "0059aa"}, [SENSOR]), ({"name": "hrm"}, [HRM]),
    ({"service": "180d"}, [HRM]), ({"min_rssi": -60}, [SENSOR]), ({"mfr": "5901"}, []),
])
async def test_scan_filters(criteria, expected):
    assert [r.address for r in await api.scan(0.1, **criteria)] == expected


async def test_scan_rejects_bad_filter():
    with pytest.raises(ValueError, match="hex"):
        await api.scan(0.1, mfr="Sensor")


async def test_find_returns_first_match_and_stops():
    r = await api.find(timeout=1, name="Sensor Demo")   # matches only once the scan response arrives
    assert r.address == SENSOR and r.name == "Sensor Demo"


async def test_find_not_found():
    with pytest.raises(api.DeviceNotFound, match="mfr='ffff'"):
        await api.find(timeout=0.1, mfr="ffff")


async def test_scan_stream_first_sighting_and_updates():
    async with aclosing(api.scan_stream(0.1)) as s:
        assert [r.address async for r in s] == [HRM, SENSOR]
    async with aclosing(api.scan_stream(0.1, updates=True)) as s:
        assert [r.address async for r in s] == [HRM, SENSOR, SENSOR]


async def test_bluetooth_unavailable():
    FakeScanner.fail = "Bluetooth adapter is turned off"
    with pytest.raises(api.BluetoothUnavailable, match="turned off"):
        await api.scan(0.1)


# -- connecting and GATT ----------------------------------------------------------
async def test_connect_by_address_read_write_services():
    async with api.connect(SENSOR) as dev:
        assert dev.is_connected and dev.mtu == 247
        assert await dev.read("2a00") == b"Fake HRM"
        assert await dev.read(3) == b"Fake HRM"          # by handle
        await dev.write("2a00", "41 42")                 # hex string
        await dev.write("6e400002-b5a3-f393-e0a9-e50e24dcca9e", b"x")
        assert client().writes == [(3, b"AB", True), (22, b"x", True)]
        svcs = dev.services()
        assert [s.name for s in svcs][:2] == ["Generic Access", "Heart Rate"]
        hr = svcs[1].characteristics[0]
        assert hr.name == "Heart Rate Measurement" and hr.properties == ("notify",)
        assert hr.descriptors[0].uuid.startswith("00002902")
        json.dumps([s.to_dict() for s in svcs])
    assert not dev.is_connected


async def test_connect_by_criteria_and_by_exact_name():
    async with api.connect(mfr="5900", scan_timeout=1) as dev:
        assert dev.address == SENSOR
    async with api.connect("Sensor Demo", scan_timeout=1) as dev:
        assert dev.address == SENSOR and dev.name == "Sensor Demo"
    with pytest.raises(api.DeviceNotFound):
        await api.connect("Sensor", scan_timeout=0.2)     # exact name: "Sensor" is not "Sensor Demo"


async def test_connect_awaitable_form():
    dev = await api.connect(SENSOR)
    assert dev.is_connected
    await dev.disconnect()
    assert not dev.is_connected


async def test_connect_failure():
    FakeClient.fail_connect = True
    with pytest.raises(api.DeviceNotFound, match="Device not found"):
        await api.connect(SENSOR)


async def test_gatt_errors_carry_the_reason():
    async with api.connect(SENSOR) as dev:
        with pytest.raises(api.GattError, match="no characteristic 2A99"):
            await dev.read("2a99")
        with pytest.raises(api.GattError, match="does not support read"):
            await dev.read("2a37")
        with pytest.raises(api.GattError, match="does not support write"):
            await dev.write("2a38", b"\x01")
    with pytest.raises(api.NotConnected):
        await dev.read("2a00")


async def test_write_error_message_from_core():
    async with api.connect(SENSOR) as dev:
        dev.session.characteristic(3).max_write_without_response_size = 1
        with pytest.raises(api.GattError, match="exceeds the maximum length"):
            await dev.write("2a00", b"too long")


async def test_notifications_queue_and_callback():
    async with api.connect(SENSOR) as dev:
        got = []
        async with dev.notifications("2a37") as n:
            assert 12 in dev.session.notifying
            client().push_notification(12, b"\x00\x48")
            assert await n.get(timeout=1) == b"\x00\x48"
            with pytest.raises(TimeoutError):
                await n.get(timeout=0.05)
        assert 12 not in dev.session.notifying

        await dev.subscribe("2a37", got.append)
        client().push_notification(12, b"\x01")
        await dev.unsubscribe("2a37")
        assert got == [b"\x01"] and 12 not in dev.session.notifying


async def test_notification_iteration_ends_on_disconnect():
    async with api.connect(SENSOR) as dev:
        async with dev.notifications("2a37") as n:
            client().push_notification(12, b"\x05")
            asyncio.get_running_loop().call_later(0.05, client().drop_link)
            assert [v async for v in n] == [b"\x05"]


async def test_pair_and_unpair():
    async with api.connect(SENSOR) as dev:
        assert await dev.pair() == "unknown"
        assert client().paired and dev.bonded is True
        await dev.unpair()
        assert not dev.is_connected      # like Windows: removing the bond drops the link


async def test_pair_failure():
    FakeClient.fail_pair = True
    async with api.connect(SENSOR) as dev:
        with pytest.raises(api.PairingFailed, match="AUTHENTICATION_FAILURE"):
            await dev.pair()


async def test_logging_goes_to_bluebench_logger(caplog):
    with caplog.at_level(logging.DEBUG, logger="bluebench"):
        async with api.connect(SENSOR) as dev:
            await dev.read("2a00")
    text = caplog.text
    assert f"{SENSOR}: Connecting to {SENSOR}" in text and "Read Device Name" in text


# -- UART -------------------------------------------------------------------------
async def test_uart_send_receive_readline():
    async with api.connect(SENSOR) as dev:
        async with dev.uart() as u:
            await u.send("hello\nwor")        # the fake echoes RX writes on TX
            assert await u.readline(timeout=1) == b"hello\n"
            assert await u.receive(timeout=1) == b"wor"
            assert await u.receive(timeout=0.05) == b""
            client().push_notification(NUS_TX_HANDLE, b"abc")
            with pytest.raises(TimeoutError):
                await u.readline(timeout=0.05)
        assert NUS_TX_HANDLE not in dev.session.notifying


async def test_uart_missing_service(monkeypatch):
    from fakes import default_gatt
    monkeypatch.setattr(FakeClient, "gatt_factory", staticmethod(lambda: default_gatt()[:2]))
    async with api.connect(SENSOR) as dev:
        with pytest.raises(api.GattError, match="no Nordic UART"):
            await dev.uart().start()


# -- macros -------------------------------------------------------------------------
ECHO = Macro("echo", [Step("subscribe", "6e400003-b5a3-f393-e0a9-e50e24dcca9e"),
                      Step("write", "6e400002-b5a3-f393-e0a9-e50e24dcca9e", "01 02"),
                      Step("expect", "6e400003-b5a3-f393-e0a9-e50e24dcca9e", "01 ??", ms=500)])
BAD = Macro("bad", [Step("read", "2a00", "FF"), Step("wait", ms=1)])


async def test_run_macro_pass_and_fail():
    async with api.connect(SENSOR) as dev:
        r = await dev.run_macro(ECHO, repeat=2)
        assert r.ok and r.summary == "completed" and len(r.steps) == 6
        assert [(s.index, s.run) for s in r.steps][-1] == (2, 1)
        r = await dev.run_macro(BAD)
        assert not r.ok and len(r.steps) == 1
        assert r.steps[0].error == "read 46-61-6B-65-20-48-52-4D, expected FF"
        json.dumps(r.to_dict())
        r = await dev.run_macro(BAD, stop_on_error=False)
        assert not r.ok and [s.ok for s in r.steps] == [False, True]
        with pytest.raises(api.MacroFailed, match="failed at step 1"):
            await dev.run_macro(BAD, check=True)


async def test_run_macro_from_file_and_library(tmp_path):
    from bluebench.core import macros
    path = tmp_path / "m.json"
    path.write_text(dumps([ECHO, BAD]), "utf-8")
    macros.library().macros.append(ECHO)
    async with api.connect(SENSOR) as dev:
        assert (await dev.run_macro(path, name="echo")).ok
        assert (await dev.run_macro("echo")).ok
        with pytest.raises(api.MacroFailed, match="pick one with name="):
            await dev.run_macro(path)
        with pytest.raises(api.MacroFailed, match="No macro named 'nope' in the library"):
            await dev.run_macro("nope")


# -- DFU --------------------------------------------------------------------------
async def test_dfu_end_to_end(tmp_path, monkeypatch):
    import test_dfu
    from bluebench.tools.dfu import secure_dfu
    from fake_dfu import FakeApp, FakeBootloader

    FakeBootloader.flash = {"expected": len(test_dfu.FW)}
    FakeBootloader.corrupt_once_at = None
    FakeBootloader.reject_init = False
    FakeApp.named.clear()

    def factory(device, disconnected_callback=None, timeout=10.0):
        addr = device if isinstance(device, str) else device.address
        cls = FakeBootloader if addr == test_dfu.BOOT_ADDR else FakeApp
        return cls(device, disconnected_callback, timeout)

    async def find(address, name, timeout=1.0):
        return address
    monkeypatch.setattr(connection, "BleakClient", factory)
    monkeypatch.setattr(secure_dfu, "find_device", find)
    zip_path = test_dfu.make_zip(tmp_path / "fw.zip")
    progress = []
    async with api.connect(test_dfu.APP_ADDR) as dev:
        r = await dev.dfu(zip_path, progress=lambda sent, total: progress.append(sent))
        assert r.ok and r.bytes == len(test_dfu.FW) and r.summary.startswith("DFU complete")
        assert progress[-1] == len(test_dfu.FW) and not dev.is_connected
    assert FakeBootloader.flash["activated"] == [test_dfu.FW]


async def test_dfu_bad_package(tmp_path):
    bad = tmp_path / "x.zip"
    bad.write_bytes(b"not a zip")
    async with api.connect(SENSOR) as dev:
        with pytest.raises(api.DfuFailed, match="Invalid DFU package"):
            await dev.dfu(bad)
        with pytest.raises(api.DfuFailed, match="no DFU service"):
            import test_dfu
            await dev.dfu(test_dfu.make_zip(tmp_path / "ok.zip"))


# -- sniffer ------------------------------------------------------------------------
async def test_sniff_writes_pcap_and_follows(tmp_path):
    import test_sniffer as ts
    from bluebench.sniffer import nrf_sniffer as ns
    from fake_air import PERIPH, adv_ind, sniffer_frame

    ports = []

    def factory(port, baud):
        ports.append(ts.FakeSerial(port, baud, answer_baud=460_800))
        return ports[-1]

    async def advertise():  # once the sniffer has been told to scan
        while not (ports and any(c.id == ns.REQ_SCAN_CONT for c in ports[-1].commands)):
            await asyncio.sleep(0.01)
        ports[-1].inject(sniffer_frame(adv_ind(PERIPH, b"\x07\x09Thingy"), ts=10))
        ports[-1].inject(sniffer_frame(adv_ind(PERIPH, b""), ts=20))
    feeder = asyncio.ensure_future(advertise())

    seen = []
    out = tmp_path / "cap.pcap"
    r = await api.sniff("COM9", 0.2, out, follow=PERIPH, serial_factory=factory,
                        on_packet=lambda p: seen.append(p.info))
    assert r.packets == 2 and r.written == 2 and r.followed == PERIPH
    assert seen[0] == 'ADV_IND "Thingy"'
    assert r.advertisers[0]["address"] == PERIPH and r.advertisers[0]["name"] == "Thingy"
    data = out.read_bytes()
    assert data[:4] == bytes.fromhex("d4c3b2a1") and len(data) > 24
    assert any(c.id == ns.REQ_FOLLOW for c in ports[-1].commands) and ports[-1].closed
    json.dumps(r.to_dict())
    await feeder


async def test_sniff_no_sniffer():
    import test_sniffer as ts
    with pytest.raises(api.SnifferError, match="No nRF Sniffer answered on COM3"):
        await api.sniff("COM3", 0.1, serial_factory=lambda p, b: ts.FakeSerial(p, b, answer_baud=0))
