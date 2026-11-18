# BlueBench command line and Python API

BlueBench's features are available without the window: `bluebench-cli` for shells, CI and
any language that can start a process, and `bluebench.api` for Python. Both use the same
code as the app, so a filter, macro or DFU package behaves identically everywhere.

Bluetooth needs a radio the OS exposes: Windows 10/11 (also Linux/macOS through bleak, not
tested). It does not work inside WSL; run Windows Python or `bluebench-cli.exe`.

- [Command line](#command-line)
- [Python API](#python-api) ([async](#async-api), [blocking](#blocking-api-sync),
  [errors](#errors), [logging](#logging))
- [Recipes](#recipes)

---

## Command line

| Where | Command |
|---|---|
| Release zip | `BlueBench\bluebench-cli.exe` |
| `pip install` of this repo | `bluebench-cli` |
| Source checkout | `py -m bluebench.cli` |

```
bluebench-cli [--json] [-v|-vv] [--timeout S] COMMAND ...
```

| Option | Meaning |
|---|---|
| `--json` | Machine output: one JSON value; streams (`notify`, `uart`, `sniff --print`) print one JSON object per line. On failure: `{"ok": false, "error": "...", "type": "GattError", "exit_code": 1}` |
| `-v`, `-vv` | Log to stderr (info, debug: every GATT operation) |
| `--timeout S` | How long to look for the device (default 10 s) |

The global options work before or after the command.

### Choosing the device

Commands that connect take a `DEVICE` (an address such as `C4:3A:7E:12:9B:01`, or the
device's exact name), **or** filters; the first device matching all of them is used:

| Filter | Matches |
|---|---|
| `--name TEXT` | name contains TEXT (case-insensitive) |
| `--mfr HEX` | manufacturer data starts with these bytes: as sent on air (`5900`, company ID low byte first) or with the company ID written as usual (`0059`). `0x`, spaces and `:` are allowed |
| `--service UUID` | advertises this service (`180d` or full UUID) |
| `--min-rssi DBM` | signal at least this strong (e.g. `-70`) |

A characteristic (`CHAR`) is a UUID (`2a00`, `6e400002-b5a3-f393-e0a9-e50e24dcca9e`) or a
handle written in hex (`0x000C`).

### Commands

| Command | What it does |
|---|---|
| `scan [-t S] [--first] [filters]` | Lists devices (strongest first). `--first` stops at the first match. |
| `services DEVICE` | Connects and prints the GATT database (handles, properties, descriptors). |
| `read DEVICE CHAR [--as FMT]` | Reads a value. |
| `write DEVICE CHAR VALUE [--as FMT] [--no-response\|--with-response]` | Writes hex bytes (default) or a value in FMT. Without a flag, it uses a write request when the characteristic supports one. |
| `notify DEVICE CHAR [--count N] [-t S] [--as FMT]` | Prints notifications/indications until N arrive, S seconds pass, or Ctrl+C. |
| `pair DEVICE [--passkey N] [--reject]` / `unpair DEVICE` | Bonds / removes the bond. `--passkey` answers devices that display one. |
| `macro list [--file F]` | Macros saved in the app (`~/.bluebench/macros.json`) or in an exported file. |
| `macro run DEVICE MACRO [--pick NAME] [--repeat N] [--keep-going]` | Runs a macro by name or from an exported `.json`, and prints PASS/FAIL per step. |
| `uart DEVICE [--send TEXT]... [--until TEXT] [-t S] [--eol lf\|crlf\|cr\|none] [--hex]` | Nordic UART. It sends `--send` texts, or stdin lines, and prints what comes back. `--until` exits as soon as the text arrives. |
| `dfu DEVICE PACKAGE.zip [--prn N]` | Nordic Secure DFU (buttonless or already in bootloader). Progress goes to stderr. |
| `sniff ports` | Serial ports; likely nRF Sniffers are marked. |
| `sniff capture [--port COM5] -t S [-o F.pcap] [--follow ADDR] [--print]` | Captures with an nRF Sniffer (experimental). |

`FMT`: `hex`, `utf8`/`text`, `u8`, `u16`/`u16le`, `u16be`, `u32`/`u32le`, `u32be`, `s8`, `s16`,
`s32`, `f32`, `bool`.

`uart` timing:
- With `--send` it listens 1 s after the last send. With `--until`, up to 10 s. `-t` changes that time.
- Without `--send` it sends stdin lines, so you can pipe a file in or type interactively.
  It ends at end of input (Ctrl+Z Enter on Windows) or on Ctrl+C.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | success |
| 1 | the operation failed: GATT error, macro failed, `--until` text not received, fewer than `--count` notifications, DFU failed, sniffer error |
| 2 | bad usage or input (missing arguments, invalid hex...) |
| 3 | no matching device (also `scan` with no results) |
| 4 | Bluetooth unavailable (adapter off or missing) |
| 130 | interrupted with Ctrl+C |

### Examples

```powershell
# Is the board nearby? (exit 3 if not)
bluebench-cli scan --first --mfr 5900 -t 10 --json

# Read the battery level as a number
bluebench-cli read --mfr 5900 2a19 --as u8

# Send a command over UART and fail unless the device answers OK within 5 s
bluebench-cli uart --mfr 5900 --send "selftest" --until "OK" -t 5

# 50 heart-rate notifications as JSON lines, saved to a file
bluebench-cli --json notify --service 180d 2a37 --count 50 > hr.jsonl

# PowerShell: use the JSON
$devices = bluebench-cli --json scan -t 5 | ConvertFrom-Json
$devices | Where-Object rssi -gt -70 | Select-Object address, name, rssi
```

---

## Python API

Each [release](https://github.com/jpdg-uy/BlueBench/releases) has the wheel attached:

```
py -m pip install bluebench-0.3.1-py3-none-any.whl
```

Or straight from the repository, or `pip install -e .` from a clone:

```
py -m pip install "git+https://github.com/jpdg-uy/BlueBench.git@v0.3.1"
```

Spanish manuals with more examples: `docs/BlueBench-CLI.pdf` and `docs/BlueBench-API-Python.pdf`.

### Async API

```python
import asyncio
from bluebench import api

async def main():
    for d in await api.scan(timeout=5, mfr="5900"):
        print(d.address, d.name, d.rssi, d.manufacturer_data)

    async with api.connect(mfr="5900") as dev:          # disconnects at the end
        name = await dev.read("2a00")
        await dev.write("6e400002-b5a3-f393-e0a9-e50e24dcca9e", "01 02")   # hex or bytes
        async with dev.notifications("2a37") as n:
            value = await n.get(timeout=5)
        async with dev.uart() as uart:
            await uart.send("status\n")
            print(await uart.readline(timeout=2))
        result = await dev.run_macro("Self test")
        assert result.ok, result.summary

asyncio.run(main())
```

#### Scanning

| Call | Returns |
|---|---|
| `await scan(timeout=5, **criteria)` | `list[ScanResult]`, strongest first |
| `async for r in scan_stream(timeout=None, updates=False, **criteria)` | each device when first seen (`updates=True`: every advertisement) |
| `await find(timeout=10, **criteria)` | the first match; raises `DeviceNotFound` |

`criteria` are `name` (substring), `address`, `mfr` (hex prefix, as on the command line),
`service`, `min_rssi` and `exact_name`. You can also pass `flt=DeviceFilter(...)`.

`ScanResult` fields:
- `address`, `name`, `rssi`
- `manufacturer_data` (`{company_id: bytes}`), `service_uuids`, `service_data`
- `tx_power`, `beacon`, `packets`
- `to_dict()` returns the same JSON as the CLI.

#### Connecting

`connect(target=None, *, scan_timeout=10, **criteria)` connects and discovers services:
- **target**: an address, an exact name, or a `ScanResult`.
- **criteria**: used instead of a target; same as for `scan`.

Use it as `async with connect(...) as dev:`, or `dev = await connect(...)` and then
`await dev.disconnect()`.

It raises `DeviceNotFound`, `ConnectFailed` or `BluetoothUnavailable`.

#### `Device`

| Member | |
|---|---|
| `address`, `name`, `is_connected`, `mtu`, `bonded`, `link` | state (`link`: interval/PHY on Windows 11) |
| `services()` | `list[Service]` → `.characteristics` → `.descriptors`, with SIG names, handles and properties; `to_dict()` |
| `await read(char)` → `bytes` | `char`: UUID string or int handle |
| `await write(char, data, response=None)` | `data`: bytes or hex string. `response=None` uses a write request if supported, otherwise a command |
| `await read_descriptor(handle)` / `await write_descriptor(handle, data)` | |
| `async with notifications(char) as n` | `await n.get(timeout)`, or `async for value in n` (ends on disconnect) |
| `await subscribe(char, callback)` / `await unsubscribe(char, callback=None)` | callback receives `bytes`; coroutine functions are scheduled |
| `await pair(passkey=None, confirm=True, prompt=None)` → protection level | `prompt(kind, pin)` decides interactively if given. `kind` is `confirm`, `display_pin`, `provide_pin` or `confirm_pin` |
| `await unpair()` | on Windows this also disconnects |
| `async with uart() as u` | `await u.send(bytes or str)`, `await u.receive(timeout)` → bytes (b"" on timeout), `await u.readline(timeout)`, `async for chunk in u` |
| `await run_macro(macro, name=None, repeat=1, stop_on_error=True, check=False)` → `MacroResult` | `macro`: a `Macro`, a library name, or an exported `.json` path (`name` picks one). `.ok`, `.summary`, `.steps[i].ok / .error`. `check=True` raises `MacroFailed` |
| `await dfu(zip_path, prn=0, progress=None)` → `DfuResult` | `progress(sent, total)`. Raises `DfuFailed`. The device ends up disconnected (it reboots) |
| `await disconnect()` | |

#### Sniffer (experimental)

`sniffer_ports()` returns the serial ports, with `.likely` set on probable nRF Sniffers.

`await sniff(port, duration, pcap_path=None, follow=None, on_packet=None)` returns a
`SniffResult`:
- `packets`, `written`, `firmware`, `advertisers`, `to_dict()`.
- `on_packet(p)` gets each decoded packet: `p.info`, `p.protocol`, `p.raw`,
  `p.air.channel` and `p.air.rssi`.

### Blocking API (`sync`)

Same calls without `await`, for scripts, pytest and notebooks:

```python
from bluebench.api import sync as bb

with bb.connect("C4:3A:7E:12:9B:01") as dev:
    dev.write("2a06", "01")
    with dev.notifications("2a37") as n:
        for value in n:                     # until the device disconnects
            print(value.hex())
```

A private asyncio loop runs in a background thread, named `bluebench-api`. Callbacks
(`subscribe`, `progress`, `on_packet`) run on that thread. Don't make blocking BlueBench
calls from inside them.

### Errors

Every failure raises a subclass of `BlueBenchError`. The message is the same one the app
shows in its log.

| Exception | When |
|---|---|
| `BluetoothUnavailable` | no adapter, or it is off |
| `DeviceNotFound` | nothing matched in time / the address is not around |
| `ConnectFailed` | found but the connection failed |
| `NotConnected` | the device disconnected (or was never connected) |
| `GattError` | missing characteristic or property, or the device rejected the operation |
| `PairingFailed`, `MacroFailed`, `DfuFailed`, `SnifferError` | as named |

Bad input (an invalid hex string, an unknown value format) raises `ValueError`. A
`notifications`/`uart` wait that runs out of time raises `TimeoutError`.

### Logging

Everything the app would log goes to the standard `logging` logger `bluebench`. Each
message is prefixed with the device address. To see it:

```python
import logging
logging.basicConfig(level=logging.INFO)   # DEBUG: every GATT operation
```

---

## Recipes

- **Production / bench test in pytest:** [examples/test_device_pytest.py](../examples/test_device_pytest.py).
- **Log notifications to CSV:** [examples/log_notifications.py](../examples/log_notifications.py).
- **CI or PowerShell check:** [examples/ci_check.ps1](../examples/ci_check.ps1).
- **Run the same check on many boards:** loop over `await api.scan(mfr=...)` and
  `connect(result)` for each one (one connection at a time is the most reliable on Windows).
