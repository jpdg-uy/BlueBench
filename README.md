# BlueBench

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Desktop Bluetooth LE workbench for Windows: scanner with advertising-data decoding, GATT
explorer, log console, Nordic UART terminal, bonding, macros, Secure DFU and a
Wireshark-style packet-capture tab (own traffic + nRF Sniffer). The same features are
available from a [command line and a Python API](#command-line-and-python-api) for scripts,
test benches and CI.

**Download:** get `BlueBench-win64.zip` from the
[latest release](https://github.com/jpdg-uy/BlueBench/releases/latest), unzip it and run
`BlueBench\BlueBench.exe`. No installation or Python needed (Windows 10/11). The user guide
(Spanish) is included in the zip.

Found a bug or have an idea? [Open an issue](https://github.com/jpdg-uy/BlueBench/issues/new/choose).

> BlueBench is an independent project. It is not affiliated with or endorsed by Nordic
> Semiconductor. nRF, nRF Connect and nRF Sniffer are trademarks of Nordic Semiconductor ASA;
> they are mentioned only to describe compatible hardware and protocols.

### Scanner extras

- Rows do not jump around: sorting by RSSI uses the average of the last few seconds, rows
  change places at most every 2 s, and ties keep discovery order. While the pointer is over
  the list the order is paused: values keep updating and new
  devices are added at the end, so the row you aim at stays put. **Min RSSI** also uses the
  average, with a 3 dB margin so devices at the threshold do not flicker in and out.
- The filter also takes hex bytes (`5900`, `0x5900`, `59 00`): it then shows devices whose
  manufacturer data starts with them, as sent on air (company ID low byte first, so 59 00 is
  company 0x0059) or with the company ID written as usual (`0059…`). The advertisement
  details show both forms (**Data** and **Raw (on air)**).
- **Bond** column: 🔒 marks devices bonded with this PC (refreshed every 10 s and right after
  pairing in the app). Devices using a changing private address may only match once connected.
- Right-click a device → **Pin to top** keeps it first (📌, bold) under any sort order, until
  you unpin it or close the app. The menu also has Connect and Copy address.

### Macros (device tab → Macros)

- **Record** appends what you do in the GATT and UART tabs (writes, reads, subscriptions) to
  the selected macro. You can also add steps by hand: Write, Read (optionally checking the
  value), Subscribe/Unsubscribe, **Expect notification** (with a timeout and a pattern such
  as `01 ?? FF`, where `??` matches any byte) and Wait.
- **Run** replays a macro on the connected device, once or ×N, with an option to stop on the
  first error. Each step is marked ✓ or ✗ and failures are explained in the log.
- Steps refer to characteristics by UUID, so a macro recorded on one board runs on any
  device with the same firmware. A notification that arrives during an earlier step still
  counts, so "write, then expect the reply" works even with fast peripherals.
- Macros are saved automatically to `%USERPROFILE%\.bluebench\macros.json`, and are shared
  by every device tab. Use **More → Export/Import** to share them as `.json` files.

### Firmware update (DFU…)

The **DFU…** button appears for devices with Nordic **Secure DFU** (nRF5 SDK bootloader):
either the bootloader itself (`DfuTarg`) or an application with the buttonless DFU service.
Pick the `.zip` made by `nrfutil pkg generate` and press **Start**.

- **Buttonless:** the app switches the device to bootloader mode, finds the bootloader
  (address + 1, or the unique name it assigns) and reconnects by itself.
- **Multi-image packages** (SoftDevice + bootloader + application) are sent in the right
  order, reconnecting between images.
- **Verification and resume:** every 4 KB object is CRC-checked and resent if needed. An
  interrupted update resumes where it stopped: start it again against the bootloader.
- **PRN** (packet receipt notification) is off by default, which is fastest. Set it to 10–20
  if transfers fail midway.
- nRF Connect SDK (MCUboot) packages are detected and rejected with an explanation: they use
  SMP/MCUmgr, not Secure DFU.

### Traffic tab (packet capture)

> The on-air (nRF Sniffer) source is **experimental**: it is covered by tests against a
> simulated sniffer but has had little real-hardware testing so far. Reports are welcome.

A Wireshark-style packet list (list, decoded details, hex dump: picking a field highlights its
bytes) with two sources that can run together:

- **App traffic**: every GATT operation BlueBench performs (reads, writes, subscriptions,
  notifications, connects, errors), across all device tabs. No extra hardware.
- **On-air**: an nRF52840 Dongle or nRF52 DK running Nordic's *nRF Sniffer for Bluetooth LE*
  firmware, on its COM port (★ = Nordic/SEGGER USB ids; baud rate is detected). It shows all
  advertising nearby; choose a device under **Follow**, then connect to it (from this app or
  any central) to capture the connection: LL control, L2CAP (reassembled), ATT with
  handle→characteristic names learned from discovery, SMP and LE signaling. Encrypted links
  show as encrypted unless the sniffer captured a legacy Just Works pairing.
- Filter with words that must all match (`att write`, `smp`, `C4:3A`, `air`, `app`);
  `!word` excludes. **Hide empty PDUs** (on by default) hides link keep-alives.
- **Export…** writes `.pcap` (on-air packets, `LINKTYPE_BLUETOOTH_LE_LL_WITH_PHDR`, opens in
  Wireshark) or `.csv` (everything shown).
- Close Wireshark first: a COM port has a single owner. `--self-test` lists the serial ports.

### Bonding and link (device tab header)

- **Pair / Unpair** stores or removes the bond in Windows. Pairing supports Just Works,
  passkey entry (type the code the device shows), passkey display (type the shown code on the
  device) and numeric comparison. **Remove bond** (Link menu) also works while disconnected.
  Windows drops the connection when a bond is removed.
- The header shows the negotiated **MTU**, the **connection interval** and the **PHY**.
  These update live when the peripheral renegotiates (Windows 11).
- **Link → Connection priority** asks Windows for *Balanced*, *High throughput* or *Low power*
  parameters (Windows 11). Windows does not let apps request an exact MTU, a specific
  interval or a PHY, so those menu items are disabled and their tooltips explain why.

Stack: Python 3.10+, [bleak](https://github.com/hbldh/bleak), PySide6, qasync, pyserial.

> Bluetooth is not available inside WSL2 — run the app with Windows Python.

## Command line and Python API

The zip also contains `BlueBench\bluebench-cli.exe` (`bluebench-cli` once installed with
pip). Every command prints readable text, or JSON with `--json` for other programs:

```
bluebench-cli scan -t 5 --mfr 5900                 # devices whose manufacturer data starts 59 00
bluebench-cli services --mfr 5900                  # connect to the first match, list its GATT
bluebench-cli read  C4:3A:7E:12:9B:01 2a00 --as utf8
bluebench-cli write "Sensor Demo" 6e400002-b5a3-f393-e0a9-e50e24dcca9e "01 02"
bluebench-cli notify --mfr 5900 2a37 --count 10 --json
bluebench-cli uart --mfr 5900 --send "status" --until "OK"
bluebench-cli macro run --mfr 5900 "Self test"     # a macro saved in the app (exit 1 if it fails)
bluebench-cli dfu C4:3A:7E:12:9B:01 app_dfu_package.zip
bluebench-cli sniff capture --port COM5 -t 30 -o capture.pcap
```

A device is an address, an exact name, or filters (`--name`, `--mfr`, `--service`,
`--min-rssi`). Exit codes: 0 ok, 1 failed, 2 bad usage, 3 device not found, 4 Bluetooth
unavailable. `bluebench-cli COMMAND --help` lists every option.

From Python, async or blocking. Install the wheel from the [latest release](https://github.com/jpdg-uy/BlueBench/releases/latest)
(`py -m pip install bluebench-<version>-py3-none-any.whl`) or this repository:

```python
from bluebench.api import sync as bb

with bb.connect(mfr="5900") as dev:
    print(dev.read("2a00"))
    dev.write("6e400002-b5a3-f393-e0a9-e50e24dcca9e", b"\x01\x02")
    with dev.notifications("2a37") as n:
        print(n.get(timeout=5))
    assert dev.run_macro("Self test").ok
```

Errors raise `bluebench.api.BlueBenchError` subclasses with the reason. The full reference
is in [docs/api.md](docs/api.md); [examples/](examples) has a pytest test, a logger and a
PowerShell CI script. Spanish manuals: `docs/BlueBench-CLI.pdf` (command line) and
`docs/BlueBench-API-Python.pdf` (Python API).

## Run from source

```
py -m pip install -e .[dev]
py -m bluebench              # the app
py -m bluebench.cli --help   # the command line
```

## Build the shareable .exe

```
powershell -ExecutionPolicy Bypass -File build_exe.ps1
```

This builds two ways to share it. Neither needs Python on the target machine
(Windows 10/11):

| Output | How to use it | Startup |
|---|---|---|
| `dist\BlueBench-win64.zip` (~35 MB) **recommended** | Unzip it, then run `BlueBench\BlueBench.exe` (or `bluebench-cli.exe` from a terminal). Keep the `_internal` folder next to them. | ~1 s |
| `dist\BlueBench.exe` (~31 MB) | Double-click the single file (GUI only). | 7–12 s |

The single exe unpacks ~80 MB to `%TEMP%` on every launch, and antivirus rescans it each
time. Self-extracting executables are also the pattern AV heuristics flag most often. The
folder version unpacks nothing: the antivirus scans each file once and caches the result.

Pass `-Layout Folder` or `-Layout OneFile` to build only one of them. The build environment
lives in `build\venv`. Pass `-Python 3.13` to build with another installed Python.

The user guide (Spanish) is `docs/BlueBench-Guia.pdf`, with `docs/BlueBench-CLI.pdf` and
`docs/BlueBench-API-Python.pdf` for the command line and the Python API; the build copies
all three next to the exe and into the zip. To regenerate them:

```
build\venv\Scripts\python.exe docs\guide\shots.py docs\guide\img   # screenshots (Windows)
python docs/guide/make_guide.py                                       # PDF (needs reportlab, pillow)
python docs/guide/make_cli_doc.py
python docs/guide/make_api_doc.py
```

Notes for people you share it with:
- **SmartScreen** will say "Windows protected your PC" because the exe is not
  code-signed: click *More info → Run anyway*.
- If Bluetooth seems not to work, run `BlueBench.exe --self-test` (either version) from a terminal (or
  a shortcut). It scans for 4 s without opening the window and writes
  `bluebench-selftest.txt` next to the exe with the result or the error.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). In short: open an issue first for larger changes,
run `pytest` before sending a pull request, and include the log for bug reports.

## Credits

- Service, characteristic, descriptor and company names come from the Bluetooth SIG
  [Assigned Numbers](https://www.bluetooth.com/specifications/assigned-numbers/).
- The nRF Sniffer wire protocol was implemented from public descriptions: Nordic's Sniffer API
  guide, Wireshark's `nordic_ble` dissector, [nrf-sniffer-cli](https://github.com/embedded-community/nrf-sniffer-cli)
  and [scapy](https://scapy.net/). No Nordic code or firmware is included.
- Built with [bleak](https://github.com/hbldh/bleak) (MIT), [PySide6](https://doc.qt.io/qtforpython-6/)
  (LGPL-3.0), [qasync](https://github.com/CabbageDevelopment/qasync) (BSD),
  [pyserial](https://github.com/pyserial/pyserial) (BSD) and [PyInstaller](https://pyinstaller.org/).

## License

[MIT](LICENSE) © 2026 Juan Pablo Di Giovanni
