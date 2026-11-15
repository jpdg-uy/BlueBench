r"""Render the user-guide screenshots with simulated devices (no radio is used).

Run with Windows Python so the images show the native look:
    build\venv\Scripts\python.exe docs\guide\shots.py docs\guide\img
"""
import asyncio, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path[:0] = [os.path.join(ROOT, "src"), os.path.join(ROOT, "tests")]
OUT = sys.argv[1]
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox
import qasync
app = QApplication([])
try:
    app.styleHints().setColorScheme(Qt.ColorScheme.Light)
except AttributeError:
    pass
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from types import SimpleNamespace as NS
from PySide6.QtCore import QRect
from bluebench.ble import connection, link, pairing
from bluebench.core import macros
from bluebench.core.macros import Macro, MacroLibrary, Step
from bluebench.tools.dfu import secure_dfu
from bluebench.ui.main_window import MainWindow
from bluebench.ui.pairing_prompt import PairingPrompt
import fake_dfu
from fake_dfu import FakeApp, FakeBootloader

macros._library = MacroLibrary(None)   # never touch the user's macros file
APP = "C4:3A:7E:12:9B:01"
BOOT = secure_dfu.bootloader_address(APP)
FW = bytes(range(256)) * 400
FakeBootloader.flash = {"expected": len(FW)}

def factory(device, disconnected_callback=None, timeout=10.0):
    addr = device if isinstance(device, str) else device.address
    c = (FakeBootloader if addr == BOOT else FakeApp)(device, disconnected_callback, timeout)
    if addr == APP:
        c.services.get_characteristic(3).value = b"Thingy Sensor"
    return c
connection.BleakClient = factory

class FakeLinkDevice:   # what Windows 11 reports for a connected device
    def get_connection_parameters(self): return NS(connection_interval=24, connection_latency=0, link_timeout=400)
    def get_connection_phy(self):
        phy = NS(is_uncoded2_m_phy=True, is_uncoded1_m_phy=False, is_coded_phy=False)
        return NS(transmit_info=phy, receive_info=phy)
    def request_preferred_connection_parameters(self, p): return NS(status=1, close=lambda: None)
async def _bonded():  # pretend the HRM strap is paired with this PC
    return {"D2:11:9C:40:6E:22"}
pairing.bonded_addresses = _bonded
link._device = lambda client: FakeLinkDevice() if client is not None else None
async def find(a, n, timeout=1): return a
secure_dfu.find_device = find

def adv(name, rssi, mfr=None, svc=None, uuids=(), tx=None):
    return AdvertisementData(name, mfr or {}, svc or {}, list(uuids), tx, rssi, ())

DEVICES = [
    (APP, "Thingy Sensor", adv("Thingy Sensor", -48, {0x0059: b"\x01\x02\x03"},
        uuids=["6e400001-b5a3-f393-e0a9-e50e24dcca9e"], tx=0)),
    ("D2:11:9C:40:6E:22", "HRM Strap", adv("HRM Strap", -61, uuids=["0000180d-0000-1000-8000-00805f9b34fb"])),
    ("E8:55:01:AA:3C:7F", "Beacon-07", adv("Beacon-07", -70, {0x004C: bytes.fromhex(
        "0215fda50693a4e24fb1afcfc6eb0764782500070002c5")})),
    ("F1:02:33:B7:88:10", None, adv(None, -77, svc={"0000feaa-0000-1000-8000-00805f9b34fb":
        bytes.fromhex("10f803676f6f676c6507")}, uuids=["0000feaa-0000-1000-8000-00805f9b34fb"])),
    ("CB:9E:44:01:D2:5A", "DfuTarg", adv("DfuTarg", -66, uuids=["0000fe59-0000-1000-8000-00805f9b34fb"])),
    ("DA:77:10:5C:E1:03", "Lab Node 3", adv("Lab Node 3", -83, {0x0059: b"\x10\x20"})),
    ("C0:FF:EE:00:12:34", None, adv(None, -91, {0x0006: b"\x01\x09\x20\x02"})),
]

def save(w, name):
    w.grab().save(os.path.join(OUT, name + ".png"))

async def settle(t=0.4):
    await asyncio.sleep(t)

async def main():
    win = MainWindow()
    win.resize(1280, 800)
    win.show()
    await settle()
    sc = win.scanner
    win.log_bus.log("info", "Scanner", "Scanning started")
    for k in range(6):
        for addr, name, a in DEVICES:
            if k % (2 if addr.startswith("C0") else 1) == 0:
                sc._on_detection(BLEDevice(addr, name, None), a._replace(rssi=a.rssi - k % 3))
        await asyncio.sleep(0.1)
    await settle(0.6)
    sv = win.scanner_view
    sv.set_pinned("DA:77:10:5C:E1:03", True)   # "Lab Node 3" pinned to the top
    await sv.refresh_bonds()
    beacon = next(r for r in range(sv.proxy.rowCount())
                  if sv.proxy.index(r, 0).data(Qt.ItemDataRole.UserRole) == "E8:55:01:AA:3C:7F")
    sv.table.selectRow(beacon)  # the iBeacon
    await settle()
    sv._on_scanning_changed(True)
    await settle()
    save(win, "01_scanner")

    win.open_device(APP)
    await settle(0.6)
    v = win.views[APP]
    await v._read_all()
    hr = v._items[12]
    v.tree.setCurrentItem(v._items[3])
    await v.session.set_notify(12, True)
    for i in range(5):
        FakeApp.push_notification(v.session._client, 12, bytes([0x00, 70 + i]))
    await settle()
    save(win, "02_gatt")

    v.pages.setCurrentWidget(v.uart_view)
    await settle()
    uv = v.uart_view
    for line in ("version", "status", "led on"):
        uv.input.setText(line)
        uv._send()
        await settle(0.1)
    v.session._client.push_notification(24, b"fw 1.4.2 (build 118)\n")
    await settle()
    save(v, "03_uart")

    lib = macros.library()
    tx, rx = "6e400003-b5a3-f393-e0a9-e50e24dcca9e", "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
    lib.macros[:] = [Macro("UART self-test", [Step("subscribe", tx),
        Step("write", rx, "70 69 6E 67 0A", response=False),
        Step("expect", tx, "70 69 6E 67 ??", ms=1000), Step("wait", ms=200),
        Step("read", "2a00")]), Macro("Heart rate check", [Step("subscribe", "2a37")])]
    lib.changed.emit()
    v.pages.setCurrentWidget(v.macro_view)
    v.macro_view.macro_list.setCurrentRow(0)
    v.macro_view.run_selected()
    await settle(0.6)
    save(v, "04_macros")

    v.pages.setCurrentIndex(0)
    v.session._set_bonded(True)
    await settle()
    v.grab(QRect(0, 0, v.width(), 44)).save(os.path.join(OUT, "05_header.png"))

    prompt = PairingPrompt(v, "Thingy Sensor")
    task = asyncio.ensure_future(prompt("confirm_pin", "482913"))
    await settle()
    save(prompt.dialogs[0], "06_pairing")
    prompt.close()
    await task

    import json, zipfile
    d = v.open_dfu()
    import tempfile
    z = os.path.join(tempfile.mkdtemp(), "app_dfu.zip")
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("app.bin", FW); zf.writestr("app.dat", b"init")
        zf.writestr("manifest.json", json.dumps({"manifest": {"application": {"bin_file": "app.bin", "dat_file": "app.dat"}}}))
    d.load_package(z)
    d.start()
    while d.dfu.running:
        await asyncio.sleep(0.01)
    # The simulated transfer is instant; show typical real-world numbers.
    d.speed.setText("100.0 / 100.0 KB · 11.8 KB/s")
    d.stage.setText("DFU complete: 100.0 KB in 8.5 s")
    d.path_edit.setText(r"C:\firmware\thingy_app_v1.4.2.zip")
    await settle()
    save(d, "07_dfu")
    d.close()

    # Traffic tab: a simulated nRF Sniffer capture of a connection, plus app traffic.
    import struct
    from bluebench.sniffer import nrf_sniffer as ns
    from bluebench.sniffer.ll_decode import LLDecoder
    from bluebench.sniffer.model import AirMeta, CapturedPacket
    from fake_air import adv_ind, connect_ind, data_pdu, l2cap, mac, ADV_AA, CRC
    CEN = "5C:F3:70:A1:22:9E"
    win.packets.clear()
    dec = LLDecoder()
    def air(ll, t, flags=0x01, ch=37, rssi=-58):
        return dec.decode(ll, AirMeta(ch, rssi, flags, int(t * 1e6)), t)
    C, P = 0x03, 0x01
    name_ad = b"\x02\x01\x06" + bytes([14, 0x09]) + b"Thingy Sensor" + b"\x03\x03\x0d\x18"
    scan_req = ADV_AA + bytes([0xC3, 12]) + mac(CEN) + mac(APP) + CRC
    rsp_ad = bytes([17, 0x07]) + bytes.fromhex("9ecadc240ee5a9e093f3a3b50100406e")
    pk = [air(adv_ind(APP, name_ad), 0.000), air(scan_req, 0.0004, ch=38),
          air(adv_ind(APP, rsp_ad, ptype=4), 0.0008, ch=38),
          air(adv_ind(APP, name_ad), 0.1001, ch=39),
          air(connect_ind(CEN, APP), 0.1005, ch=39),
          air(data_pdu(3, bytes([0x0C, 12]) + struct.pack("<HH", 0x0002, 0x410C)), 0.1342, C, 9),
          air(data_pdu(3, bytes([0x0C, 13]) + struct.pack("<HH", 0x0059, 0x0003)), 0.1344, P, 9),
          air(data_pdu(1, b""), 0.164, C, 21),
          air(data_pdu(3, bytes([0x14]) + struct.pack("<HHHH", 251, 2120, 251, 2120)), 0.194, C, 33),
          air(data_pdu(3, bytes([0x15]) + struct.pack("<HHHH", 251, 2120, 251, 2120)), 0.1942, P, 33),
          air(data_pdu(2, l2cap(4, b"\x02\xf7\x00")), 0.224, C, 8),
          air(data_pdu(2, l2cap(4, b"\x03\xf7\x00")), 0.2242, P, 8),
          air(data_pdu(2, l2cap(4, b"\x10\x01\x00\xff\xff\x00\x28")), 0.254, C, 20),
          air(data_pdu(2, l2cap(4, b"\x11\x06\x01\x00\x07\x00\x00\x18\x0a\x00\x0f\x00\x0d\x18")), 0.2542, P, 20),
          air(data_pdu(2, l2cap(4, b"\x08\x0a\x00\x0f\x00\x03\x28")), 0.284, C, 32),
          air(data_pdu(2, l2cap(4, b"\x09\x07\x0b\x00\x10\x0c\x00\x37\x2a\x0e\x00\x02\x0f\x00\x38\x2a")), 0.2842, P, 32),
          air(data_pdu(2, l2cap(4, b"\x0a\x0f\x00")), 0.314, C, 5),
          air(data_pdu(2, l2cap(4, b"\x0b\x01")), 0.3142, P, 5),
          air(data_pdu(2, l2cap(4, b"\x12\x0d\x00\x01\x00")), 0.344, C, 17),
          air(data_pdu(2, l2cap(4, b"\x13")), 0.3442, P, 17)]
    for i in range(4):
        pk.append(air(data_pdu(2, l2cap(4, bytes([0x1b, 0x0c, 0x00, 0x00, 72 + i]))), 1.374 + i, P, (3 + 11 * i) % 37))
        pk.append(air(data_pdu(1, b""), 1.3742 + i, C, (3 + 11 * i) % 37))
    pk.append(air(data_pdu(2, l2cap(4, b"\x0a\x20\x00")), 5.41, C, 12))
    pk.append(air(data_pdu(2, l2cap(4, b"\x01\x0a\x20\x00\x05")), 5.4102, P, 12))
    win.packets.add_many(pk)
    win.packets.add(CapturedPacket("app", 5.42, APP, "BlueBench", "GATT",
                                   "Read failed: Insufficient Authentication (0x05)", error=True))
    tv = win.traffic_view
    air_cap = win.air_capture
    air_cap.advertisers[APP] = ns.Advertiser(APP, True, "Thingy Sensor", -58, 40, True)
    air_cap.state, air_cap.following = "following", APP
    tv.port_combo.clear()
    tv.port_combo.addItem("COM7 — nRF52840 Dongle (nRF Sniffer)  ★", "COM7")
    tv._refresh_follow()
    tv._on_air_state("following")
    win.tabs.setCurrentWidget(tv)
    win.log_dock.hide()
    await settle()
    row = next(i for i, p in enumerate(tv.model.rows) if "Notification" in p.info)
    tv.table.selectRow(row)
    await settle()
    att = next(i for i in range(tv.details.topLevelItemCount())
               if tv.details.topLevelItem(i).text(0).startswith("Attribute"))
    item = tv.details.topLevelItem(att)
    item.setExpanded(True)
    tv.details.setCurrentItem(item.child(item.childCount() - 1))
    await settle()
    tv.table.scrollTo(tv.model.index(row, 0))
    save(win, "08_traffic")
    win.log_dock.show()
    air_cap.state = "idle"

loop = qasync.QEventLoop(app)
asyncio.set_event_loop(loop)
loop.run_until_complete(main())
os._exit(0)
