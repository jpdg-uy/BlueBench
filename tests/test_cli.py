"""bluebench-cli, run in-process through cli.main(argv) against fake bleak objects."""
import asyncio
import io
import json

import pytest

from bluebench import __version__, cli
from bluebench.ble import connection, pairing, scanner
from bluebench.core.macros import Macro, Step, dumps
from fakes import NUS_RX_HANDLE, FakeClient, FakeScanner, advert

SENSOR = "C4:3A:00:00:00:01"
HRM = "C4:3A:00:00:00:02"


@pytest.fixture(autouse=True)
def fake_ble(monkeypatch):
    FakeClient.instances.clear()
    FakeClient.unpaired.clear()
    FakeClient.fail_connect = FakeClient.fail_pair = False
    FakeScanner.fail = None
    FakeScanner.adverts = [advert(HRM, "Fake HRM", -70, services=["180d"]),
                           advert(SENSOR, "Sensor Demo", -50, mfr={0x0059: b"\xaa\xbb"})]
    monkeypatch.setattr(scanner, "BleakScanner", FakeScanner)
    monkeypatch.setattr(connection, "BleakClient", FakeClient)
    monkeypatch.setattr(pairing, "BleakClient", FakeClient)
    monkeypatch.setattr(pairing, "_is_winrt", lambda: False)


def run(capsys, *argv):
    code = cli.main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def run_json(capsys, *argv):
    code, out, err = run(capsys, "--json", *argv)
    return code, [json.loads(line) for line in out.splitlines()], err


def client() -> FakeClient:
    return FakeClient.instances[-1]


def test_version_and_help(capsys):
    code, out, _ = run(capsys, "--version")
    assert code == 0 and out.strip() == f"bluebench-cli {__version__}"
    code, out, _ = run(capsys, "--help")
    assert code == 0 and "Exit codes" in out and "sniff" in out


# -- scan -------------------------------------------------------------------------
def test_scan_json_and_table(capsys):
    code, (devices,), _ = run_json(capsys, "scan", "-t", "0.1")
    assert code == 0 and [d["address"] for d in devices] == [SENSOR, HRM]
    assert devices[0]["manufacturer_data"][0]["raw"] == "5900aabb"
    code, out, _ = run(capsys, "scan", "-t", "0.1", "--mfr", "5900")
    assert code == 0 and "Sensor Demo" in out and "raw 5900aabb" in out and "Fake HRM" not in out


def test_scan_none_matches_exits_3(capsys):
    code, (devices,), _ = run_json(capsys, "scan", "-t", "0.1", "--mfr", "ffff")
    assert code == 3 and devices == []


def test_scan_first(capsys):
    code, (d,), _ = run_json(capsys, "scan", "--first", "--service", "180d")
    assert code == 0 and d["address"] == HRM
    code, (err,), _ = run_json(capsys, "scan", "--first", "--name", "nope", "-t", "0.1")
    assert code == 3 and err["type"] == "DeviceNotFound" and err["exit_code"] == 3


def test_bluetooth_unavailable_exits_4(capsys):
    FakeScanner.fail = "Bluetooth adapter is turned off"
    code, out, err = run(capsys, "scan", "-t", "0.1")
    assert code == 4 and "turned off" in err and out == ""


def test_bad_filter_is_usage_error(capsys):
    code, _, err = run(capsys, "scan", "--mfr", "Sensor")
    assert code == 2 and "hex" in err


# -- GATT -------------------------------------------------------------------------
def test_services(capsys):
    code, (r,), _ = run_json(capsys, "services", SENSOR)
    assert code == 0 and r["mtu"] == 247
    assert r["services"][1]["characteristics"][0] == {
        "uuid": "2A37", "name": "Heart Rate Measurement", "handle": 12, "properties": ["notify"],
        "descriptors": [{"uuid": "2902", "name": r["services"][1]["characteristics"][0]
                         ["descriptors"][0]["name"], "handle": 13}]}
    code, out, _ = run(capsys, "services", "--mfr", "5900")
    assert code == 0 and "Heart Rate (180D)" in out and "[notify]" in out


def test_read_formats_and_handle(capsys):
    code, (r,), _ = run_json(capsys, "read", SENSOR, "2a00", "--as", "utf8")
    assert code == 0 and r == {"char": "2a00", "hex": "46616b652048524d", "value": "Fake HRM"}
    code, out, _ = run(capsys, "read", SENSOR, "0x0003")
    assert code == 0 and out.strip() == 'Device Name (2A00): 46 61 6B 65 20 48 52 4D  "Fake HRM"'
    code, out, _ = run(capsys, "read", SENSOR, "2a38", "--as", "u8")
    assert code == 0 and out.strip() == "1"


def test_read_by_name_filter_device_positional_optional(capsys):
    code, out, _ = run(capsys, "read", "--name", "sensor", "2a00", "--as", "text")
    assert code == 0 and out.strip() == "Fake HRM"


def test_read_errors(capsys):
    code, _, err = run(capsys, "read", SENSOR, "2a99")
    assert code == 1 and "no characteristic 2A99" in err
    code, _, err = run(capsys, "read", "2a00")       # that is CHAR; no DEVICE given
    assert code == 2 and "give a DEVICE" in err
    code, _, err = run(capsys, "read", "Nobody", "2a00", "--timeout", "0.2")
    assert code == 3 and "name='Nobody'" in err
    code, _, err = run(capsys, "read", SENSOR, "0xZZ")
    assert code == 2 and "bad handle" in err
    FakeClient.fail_connect = True
    code, (e,), _ = run_json(capsys, "read", SENSOR, "2a00")
    assert code == 3 and "Device not found" in e["error"]


def test_device_and_filters_are_exclusive(capsys):
    code, _, err = run(capsys, "read", SENSOR, "2a00", "--mfr", "5900")
    assert code == 2 and "not both" in err


def test_write_variants(capsys):
    assert run(capsys, "write", SENSOR, "2a00", "41:42")[0] == 0
    assert client().writes == [(3, b"AB", True)]
    assert run(capsys, "write", SENSOR, "2a00", "hi", "--as", "utf8")[0] == 0
    assert client().writes == [(3, b"hi", True)]
    assert run(capsys, "write", SENSOR, "6e400002-b5a3-f393-e0a9-e50e24dcca9e", "01",
               "--no-response")[0] == 0
    assert client().writes == [(NUS_RX_HANDLE, b"\x01", False)]
    code, _, err = run(capsys, "write", SENSOR, "2a00", "ABC")
    assert code == 2 and "even number" in err
    code, _, err = run(capsys, "write", SENSOR, "2a00", "300", "--as", "u8")
    assert code == 2 and "out of range" in err


class PushingClient(FakeClient):
    """Sends ``pushes`` notifications shortly after a subscription."""
    pushes = [b"\x00\x48", b"\x00\x49"]

    async def start_notify(self, ch, callback):
        await super().start_notify(ch, callback)
        loop = asyncio.get_running_loop()
        for i, value in enumerate(self.pushes):
            loop.call_later(0.02 * (i + 1), lambda v=value: self.push_notification(ch.handle, v))


def test_notify_count_and_duration(capsys, monkeypatch):
    monkeypatch.setattr(connection, "BleakClient", PushingClient)
    code, lines, _ = run_json(capsys, "notify", SENSOR, "2a37", "--count", "2", "--as", "u16be")
    assert code == 0 and [(x["hex"], x["value"]) for x in lines] == [("0048", "72"), ("0049", "73")]
    code, out, err = run(capsys, "notify", SENSOR, "2a37", "--count", "3", "-t", "0.2")
    assert code == 1 and "got 2 of 3" in err and out.count("00 4") == 2
    code, out, _ = run(capsys, "notify", SENSOR, "2a37", "-t", "0.2")
    assert code == 0 and len(out.splitlines()) == 2


def test_pair_unpair(capsys):
    code, (r,), _ = run_json(capsys, "pair", SENSOR)
    assert code == 0 and r == {"address": SENSOR, "bonded": True, "protection": "unknown"}
    assert run(capsys, "unpair", SENSOR)[0] == 0 and FakeClient.unpaired == [SENSOR]
    FakeClient.fail_pair = True
    code, _, err = run(capsys, "pair", SENSOR)
    assert code == 1 and "AUTHENTICATION_FAILURE" in err


# -- macros -------------------------------------------------------------------------
NUS_TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
NUS_RX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
ECHO = Macro("echo", [Step("subscribe", NUS_TX), Step("write", NUS_RX, "01 02"),
                      Step("expect", NUS_TX, "01 ??", ms=500)])
BAD = Macro("bad", [Step("read", "2a00", "FF")])


def test_macro_list_and_run(capsys, tmp_path):
    path = tmp_path / "macros.json"
    path.write_text(dumps([ECHO, BAD]), "utf-8")
    code, (listed,), _ = run_json(capsys, "macro", "list", "--file", str(path))
    assert code == 0 and [m["name"] for m in listed] == ["echo", "bad"]
    code, (r,), _ = run_json(capsys, "macro", "run", SENSOR, str(path), "--pick", "echo",
                             "--repeat", "2")
    assert code == 0 and r["ok"] and len(r["steps"]) == 6
    code, out, _ = run(capsys, "macro", "run", SENSOR, str(path), "--pick", "bad")
    assert code == 1 and "FAIL  1. Read Device Name, expect FF" in out and "expected FF" in out
    FakeClient.instances.clear()
    code, _, err = run(capsys, "macro", "run", SENSOR, str(path))
    assert code == 1 and "pick one" in err
    assert not FakeClient.instances    # the macro is checked before connecting


def test_macro_run_from_library(capsys):
    from bluebench.core import macros
    macros.library().macros.append(ECHO)
    code, out, _ = run(capsys, "macro", "run", "--mfr", "5900", "echo")
    assert code == 0 and "Macro 'echo': completed" in out
    code, out, _ = run(capsys, "macro", "list")
    assert "echo  (3 steps)" in out


# -- UART -------------------------------------------------------------------------
def test_uart_send_until(capsys):
    code, out, _ = run(capsys, "uart", SENSOR, "--send", "hello", "--until", "hello")
    assert code == 0 and out == "hello\n"               # the fake echoes what it gets
    assert client().writes[-1] == (NUS_RX_HANDLE, b"hello\n", False)
    code, _, err = run(capsys, "uart", SENSOR, "--send", "a", "--until", "zzz", "-t", "0.1")
    assert code == 1 and "'zzz' not received" in err


def test_uart_stdin_json_and_hex(capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("one\ntwo\n"))
    code, lines, _ = run_json(capsys, "uart", SENSOR, "--eol", "crlf", "-t", "0.1")
    assert code == 0 and "".join(x["text"] for x in lines) == "one\r\ntwo\r\n"
    code, out, _ = run(capsys, "uart", SENSOR, "--hex", "--send", "01 02", "-t", "0.1")
    assert code == 0 and out.strip() == "01 02"
    code, _, err = run(capsys, "uart", SENSOR, "--hex", "--send", "xyz", "-t", "0.1")
    assert code == 2 and "not hex" in err


# -- DFU and sniffer ------------------------------------------------------------------
def test_dfu_bad_package(capsys, tmp_path):
    bad = tmp_path / "x.zip"
    bad.write_bytes(b"nope")
    code, (e,), _ = run_json(capsys, "dfu", SENSOR, str(bad))
    assert code == 1 and e["type"] == "DfuFailed"


def test_sniff_ports_and_capture(capsys, monkeypatch, tmp_path):
    import test_sniffer as ts
    from bluebench.sniffer import nrf_sniffer as ns
    monkeypatch.setattr(ns, "list_ports", lambda: [ns.PortInfo("COM7", "nRF Sniffer", True),
                                                   ns.PortInfo("COM1", "Serial", False)])
    code, (ports,), _ = run_json(capsys, "sniff", "ports")
    assert code == 0 and ports[0] == {"port": "COM7", "description": "nRF Sniffer",
                                      "likely_sniffer": True, "bootloader": False}
    monkeypatch.setattr(ns, "open_serial", lambda p, b: ts.FakeSerial(p, b, answer_baud=460_800))
    out = tmp_path / "c.pcap"
    code, (r,), _ = run_json(capsys, "sniff", "capture", "-t", "0.1", "-o", str(out))
    assert code == 0 and r["pcap"] == str(out) and out.exists()
    monkeypatch.setattr(ns, "open_serial", lambda p, b: ts.FakeSerial(p, b, answer_baud=0))
    code, _, err = run(capsys, "sniff", "capture", "--port", "COM3", "-t", "0.1")
    assert code == 1 and "No nRF Sniffer answered on COM3" in err


def test_pair_prompts(capsys, monkeypatch):
    answers = {}

    async def fake_pair(client, prompt, log=None):
        answers["display"] = await prompt("display_pin", "123456")
        answers["compare"] = await prompt("confirm_pin", "654321")
        answers["provide"] = await prompt("provide_pin", "")
        return "encryption and authentication"
    monkeypatch.setattr(pairing, "pair", fake_pair)
    code, out, err = run(capsys, "pair", SENSOR, "--passkey", "111222", "--reject")
    assert code == 0 and "(encryption and authentication)" in out
    assert "Type passkey 123456 on the device" in err and "654321 -> reject" in err
    assert answers == {"display": True, "compare": False, "provide": "111222"}


def test_errors_reported_once_unless_verbose(capsys):
    code, _, err = run(capsys, "read", SENSOR, "2a37")
    assert code == 1 and err.count("does not support read") == 1 and "ERROR" not in err
    code, _, err = run(capsys, "-v", "read", SENSOR, "2a00")
    assert code == 0 and "INFO" in err and "Connecting to" in err


def test_sniff_capture_points_at_a_dongle_in_its_bootloader(capsys, monkeypatch):
    from bluebench.sniffer import nrf_sniffer as ns
    monkeypatch.setattr(ns, "list_ports", lambda: [
        ns.PortInfo("COM3", "nRF52 SDFU USB (COM3)", False, bootloader=True)])
    code, _, err = run(capsys, "sniff", "capture", "-t", "0.1")
    assert code == 2 and "COM3 is a Nordic DFU bootloader" in err


def test_sniff_capture_picks_the_port_that_answers(capsys, monkeypatch):
    import test_sniffer as ts
    from bluebench.sniffer import nrf_sniffer as ns
    monkeypatch.setattr(ns, "list_ports", lambda: [ns.PortInfo("COM4", "USB Serial Device", True),
                                                   ns.PortInfo("COM5", "USB Serial Device", True)])
    monkeypatch.setattr(ns, "open_serial", lambda p, b: ts.FakeSerial(p, b, no_cts=p == "COM4"))
    code, _, err = run(capsys, "sniff", "capture", "-t", "0.1")
    assert code == 0 and "Capturing on COM5" in err
    monkeypatch.setattr(ns, "open_serial", lambda p, b: ts.FakeSerial(p, b, answer_baud=0))
    code, _, err = run(capsys, "sniff", "capture", "-t", "0.1")
    assert code == 2 and "found COM4, COM5), none answered" in err
