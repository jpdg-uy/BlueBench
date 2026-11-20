"""BlueBench command line: ``bluebench-cli <command> ...`` (or ``python -m bluebench.cli``).

Human-readable output by default; ``--json`` prints JSON (one object per line for
streams) for scripts. Logs go to stderr (``-v``, ``-vv``). Exit codes:

  0  success
  1  the operation or check failed (GATT error, macro failed, --until not seen...)
  2  bad usage or input
  3  no matching device found
  4  Bluetooth unavailable
  130 interrupted (Ctrl+C)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from collections.abc import Callable
from typing import Any

from . import __version__, api
from .ble import registry, uuids
from .ble.formats import ValueFormat, decode, encode

EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_NOT_FOUND, EXIT_NO_BLUETOOTH, EXIT_INTERRUPTED = \
    0, 1, 2, 3, 4, 130

FORMATS = {
    "hex": ValueFormat.HEX, "utf8": ValueFormat.UTF8, "text": ValueFormat.UTF8,
    "u8": ValueFormat.UINT8, "u16": ValueFormat.UINT16_LE, "u16le": ValueFormat.UINT16_LE,
    "u16be": ValueFormat.UINT16_BE, "u32": ValueFormat.UINT32_LE, "u32le": ValueFormat.UINT32_LE,
    "u32be": ValueFormat.UINT32_BE, "s8": ValueFormat.SINT8, "s16": ValueFormat.SINT16_LE,
    "s32": ValueFormat.SINT32_LE, "f32": ValueFormat.FLOAT32_LE, "bool": ValueFormat.BOOL,
}
EOLS = {"lf": b"\n", "crlf": b"\r\n", "cr": b"\r", "none": b""}
GLOBAL_DEFAULTS = {"json": False, "verbose": 0, "timeout": 10.0}


class UsageError(Exception):
    pass


# -- output -------------------------------------------------------------------
class Out:
    def __init__(self, as_json: bool) -> None:
        self.json = as_json

    def emit(self, obj: Any, human: Callable[[], str] | str | None = None) -> None:
        """One result: JSON object/list in --json mode, else the human text."""
        if self.json:
            print(json.dumps(obj, ensure_ascii=False), flush=True)
        elif human is not None:
            text = human() if callable(human) else human
            if text:
                print(text, flush=True)


def _stamp() -> str:
    t = time.time()
    return time.strftime("%H:%M:%S", time.localtime(t)) + f".{int(t * 1000) % 1000:03d}"


def _hex(data: bytes) -> str:
    return data.hex(" ").upper() if data else "(empty)"


def _text_hint(data: bytes) -> str:
    try:
        s = data.decode("utf-8")
    except UnicodeDecodeError:
        return ""
    return f'  "{s}"' if s and s.isprintable() else ""


def _char_label(dev: api.Device, char) -> str:
    ch = dev._char(char)
    return f"{uuids.display_name(ch.uuid, 'characteristics')} ({uuids.short_form(ch.uuid)})"


def _value_obj(char: str, data: bytes, fmt: ValueFormat | None) -> dict:
    obj: dict[str, Any] = {"char": char, "hex": data.hex()}
    if fmt is not None:
        obj["value"] = decode(data, fmt)
    return obj


def _char_arg(text: str):
    """Characteristic: a UUID ("2a00", "180d", full), or a handle written 0x000C."""
    t = text.strip()
    if t.lower().startswith("0x"):
        try:
            return int(t, 16)
        except ValueError:
            raise UsageError(f"bad handle {t!r} (write handles as 0x000C)") from None
    return t


# -- device selection ----------------------------------------------------------
def _criteria(args) -> dict:
    keys = ["name", "mfr", "service", "min_rssi", *registry.filter_fields()]
    c = {k: getattr(args, k.replace("-", "_")) for k in keys
         if getattr(args, k.replace("-", "_"), None) is not None}
    return c


def _connect(args):
    criteria = _criteria(args)
    if args.device is None and not criteria:
        raise UsageError("give a DEVICE (address or exact name) or --name/--mfr/--service")
    if args.device is not None and criteria:
        raise UsageError("give either a DEVICE or filters (--name/--mfr/--service), not both")
    return api.connect(args.device, scan_timeout=args.timeout, **criteria)


# -- commands ----------------------------------------------------------------
async def cmd_scan(args, out: Out) -> int:
    criteria = _criteria(args)
    if args.first:
        r = await api.find(args.timeout if args.duration is None else args.duration, **criteria)
        out.emit(r.to_dict(), lambda: _scan_table([r]))
        return EXIT_OK
    results = await api.scan(args.duration or 5.0, **criteria)
    out.emit([r.to_dict() for r in results], lambda: _scan_table(results))
    return EXIT_OK if results else EXIT_NOT_FOUND


def _scan_table(results: list[api.ScanResult]) -> str:
    if not results:
        return "No devices found."
    rows = [f"{'ADDRESS':<17}  {'RSSI':>4}  {'NAME':<24}  DETAILS"]
    for r in results:
        details = []
        for cid, payload in r.manufacturer_data.items():
            raw = cid.to_bytes(2, "little") + payload
            company = uuids.company_name(cid)
            label = f"0x{cid:04X}" if company.startswith("Unknown") else f"{company} <0x{cid:04X}>"
            details.append(f"{label} raw {raw.hex()}")
        if r.beacon:
            details.append(r.beacon)
        if r.service_uuids:
            details.append("services " + ",".join(uuids.short_form(u) for u in r.service_uuids))
        rows.append(f"{r.address:<17}  {r.rssi:>4}  {(r.name or '-')[:24]:<24}  {'; '.join(details)}")
    return "\n".join(rows)


async def cmd_services(args, out: Out) -> int:
    async with _connect(args) as dev:
        svcs = dev.services()

        def human() -> str:
            lines = [f"{dev.name} ({dev.address}), MTU {dev.mtu or '?'}"]
            for s in svcs:
                lines.append(f"{s.name} ({uuids.short_form(s.uuid)})  handle 0x{s.handle:04X}")
                for c in s.characteristics:
                    lines.append(f"  {c.name} ({uuids.short_form(c.uuid)})  handle 0x{c.handle:04X}"
                                 f"  [{', '.join(c.properties)}]")
                    for d in c.descriptors:
                        lines.append(f"    {d.name} ({uuids.short_form(d.uuid)})  handle 0x{d.handle:04X}")
            return "\n".join(lines)
        out.emit({"address": dev.address, "name": dev.name, "mtu": dev.mtu,
                  "services": [s.to_dict() for s in svcs]}, human)
    return EXIT_OK


async def cmd_read(args, out: Out) -> int:
    fmt = FORMATS[args.as_] if args.as_ else None
    char = _char_arg(args.char)
    async with _connect(args) as dev:
        data = await dev.read(char)
        label = _char_label(dev, char)
    out.emit(_value_obj(args.char, data, fmt),
             lambda: decode(data, fmt) if fmt else f"{label}: {_hex(data)}{_text_hint(data)}")
    return EXIT_OK


async def cmd_write(args, out: Out) -> int:
    fmt = FORMATS[args.as_ or "hex"]
    try:
        data = encode(args.value, fmt)
    except ValueError as exc:
        raise UsageError(str(exc)) from None
    response = False if args.no_response else (True if args.with_response else None)
    char = _char_arg(args.char)
    async with _connect(args) as dev:
        await dev.write(char, data, response=response)
        label = _char_label(dev, char)
    out.emit({"char": args.char, "hex": data.hex(), "ok": True},
             f"Wrote {_hex(data)} to {label}")
    return EXIT_OK


async def cmd_notify(args, out: Out) -> int:
    fmt = FORMATS[args.as_] if args.as_ else None
    char = _char_arg(args.char)
    received = 0
    async with _connect(args) as dev:
        label = _char_label(dev, char)
        loop = asyncio.get_running_loop()
        deadline = None if args.duration is None else loop.time() + args.duration
        if not out.json:
            print(f"Listening to {label}" + (f" for {args.duration:g} s" if args.duration else
                                             " (Ctrl+C to stop)"), file=sys.stderr, flush=True)
        async with dev.notifications(char) as n:
            while args.count is None or received < args.count:
                remaining = None if deadline is None else deadline - loop.time()
                if remaining is not None and remaining <= 0:
                    break
                try:
                    value = await n.get(timeout=remaining)
                except TimeoutError:
                    break
                received += 1
                obj = _value_obj(args.char, value, fmt) | {"time": time.time()}
                out.emit(obj, lambda: f"{_stamp()}  {decode(value, fmt) if fmt else _hex(value)}")
    if args.count is not None and received < args.count:
        print(f"error: got {received} of {args.count} notifications", file=sys.stderr)
        return EXIT_FAILED
    return EXIT_OK


async def cmd_pair(args, out: Out) -> int:
    loop = asyncio.get_running_loop()

    async def prompt(kind: str, pin: str):
        if kind == "display_pin":
            print(f"Type passkey {pin} on the device", file=sys.stderr, flush=True)
            return True
        if kind == "confirm_pin":
            print(f"Numeric comparison: {pin} -> {'reject' if args.reject else 'accept'}",
                  file=sys.stderr, flush=True)
            return not args.reject
        if kind == "provide_pin":
            if args.passkey is not None:
                return args.passkey
            if sys.stdin is not None and sys.stdin.isatty():
                return (await loop.run_in_executor(
                    None, input, "Passkey shown on the device: ")).strip() or None
            print("The device asks for a passkey: give --passkey", file=sys.stderr, flush=True)
            return None
        return True   # Just Works

    async with _connect(args) as dev:
        level = await dev.pair(prompt=prompt)
        out.emit({"address": dev.address, "bonded": True, "protection": level},
                 f"Bonded with {dev.address} ({level})")
    return EXIT_OK


async def cmd_unpair(args, out: Out) -> int:
    async with _connect(args) as dev:
        await dev.unpair()
        out.emit({"address": dev.address, "bonded": False}, f"Removed the bond with {dev.address}")
    return EXIT_OK


async def cmd_macro_list(args, out: Out) -> int:
    from .core import macros as m
    if args.file:
        try:
            found = m.loads(open(args.file, encoding="utf-8").read())
        except (OSError, ValueError, TypeError) as exc:
            raise api.MacroFailed(f"Could not load macros from {args.file}: {exc}") from None
    else:
        lib = m.library()
        if lib.load_error:
            raise api.MacroFailed(lib.load_error)
        found = lib.macros

    def human() -> str:
        if not found:
            return "No macros saved." if not args.file else f"No macros in {args.file}."
        return "\n".join(f"{x.name}  ({len(x.steps)} steps)\n" +
                         "\n".join(f"  {i + 1}. {s.describe()}" for i, s in enumerate(x.steps))
                         for x in found)
    out.emit([x.to_dict() for x in found], human)
    return EXIT_OK


async def cmd_macro_run(args, out: Out) -> int:
    macro = api.load_macro(args.macro, args.pick)   # fail before connecting
    async with _connect(args) as dev:
        result = await dev.run_macro(macro, repeat=args.repeat,
                                     stop_on_error=not args.keep_going)

    def human() -> str:
        lines = []
        for s in result.steps:
            run = f"[run {s.run + 1}] " if args.repeat > 1 else ""
            mark = "PASS" if s.ok else "FAIL"
            lines.append(f"{mark}  {run}{s.index + 1}. {s.description}"
                         + (f"  -> {s.error}" if s.error else ""))
        lines.append(f"Macro '{result.name}': {result.summary} ({result.seconds:.1f} s)")
        return "\n".join(lines)
    out.emit(result.to_dict(), human)
    return EXIT_OK if result.ok else EXIT_FAILED


def _stdin_lines(loop: asyncio.AbstractEventLoop) -> asyncio.Queue:
    """Lines from stdin (a pipe or the terminal), None at EOF. A daemon thread, so it
    works on Windows consoles and never blocks the exit."""
    import threading
    q: asyncio.Queue = asyncio.Queue()

    def reader() -> None:
        for line in iter(sys.stdin.readline, ""):
            loop.call_soon_threadsafe(q.put_nowait, line)
        loop.call_soon_threadsafe(q.put_nowait, None)
    threading.Thread(target=reader, name="stdin", daemon=True).start()
    return q


async def cmd_uart(args, out: Out) -> int:
    eol = EOLS[args.eol]
    loop = asyncio.get_running_loop()
    matched = False

    def payload(text: str) -> bytes:
        if args.hex:
            try:
                return bytes.fromhex(text.replace(" ", ""))
            except ValueError:
                raise UsageError(f"not hex: {text!r}") from None
        return text.encode("utf-8") + eol

    async with _connect(args) as dev:
        async with dev.uart() as u:
            async def sender() -> None:
                for text in args.send or []:
                    await u.send(payload(text))
                if args.send:
                    return
                lines = _stdin_lines(loop)
                while (line := await lines.get()) is not None:
                    await u.send(payload(line.rstrip("\r\n")))

            send_task = asyncio.ensure_future(sender())
            seen = bytearray()
            # After the last input: listen this long (longer when waiting for --until).
            linger = args.duration if args.duration is not None else (10.0 if args.until else 1.0)
            deadline = None
            while True:
                if send_task.done():
                    send_task.result()      # re-raise send errors
                    if deadline is None:
                        deadline = loop.time() + linger
                remaining = None if deadline is None else deadline - loop.time()
                if remaining is not None and remaining <= 0:
                    break
                data = await u.receive(timeout=0.2 if remaining is None else min(0.2, remaining))
                if data:
                    seen += data
                    if out.json:
                        out.emit({"time": time.time(), "hex": data.hex(),
                                  "text": data.decode("utf-8", errors="replace")})
                    else:
                        sys.stdout.write(data.hex(" ") + "\n" if args.hex
                                         else data.decode("utf-8", errors="replace"))
                        sys.stdout.flush()
                    if args.until and args.until.encode("utf-8") in seen:
                        matched = True
                        break
                if not dev.is_connected:
                    raise api.NotConnected("Device disconnected")
            send_task.cancel()
    if args.until and not matched:
        print(f"\nerror: '{args.until}' not received", file=sys.stderr)
        return EXIT_FAILED
    return EXIT_OK


async def cmd_dfu(args, out: Out) -> int:
    last = {"pct": -1}

    def progress(sent: int, total: int) -> None:
        pct = sent * 100 // total if total else 100
        if pct != last["pct"]:
            last["pct"] = pct
            if out.json:
                out.emit({"event": "progress", "sent": sent, "total": total})
            else:
                print(f"\rDFU {pct:3d}%  {sent}/{total} bytes", end="", file=sys.stderr, flush=True)
    async with _connect(args) as dev:
        result = await dev.dfu(args.package, prn=args.prn, progress=progress)
    if not out.json:
        print(file=sys.stderr)
    out.emit(result.to_dict(), result.summary)
    return EXIT_OK


async def cmd_sniff_ports(args, out: Out) -> int:
    ports = api.sniffer_ports()
    out.emit([{"port": p.device, "description": p.description, "likely_sniffer": p.likely,
               "bootloader": p.bootloader} for p in ports],
             lambda: "\n".join(p.label for p in ports) or "No serial ports found.")
    return EXIT_OK


async def cmd_sniff_capture(args, out: Out) -> int:
    port = args.port
    if port is None:
        ports = api.sniffer_ports()
        likely = [p for p in ports if p.likely]
        if len(likely) > 1:
            port = await api.find_sniffer()
        elif likely:
            port = likely[0].device
        if port is None:
            boot = [p.device for p in ports if p.bootloader]
            hint = (f"; {', '.join(boot)} is a Nordic DFU bootloader: flash the nRF Sniffer"
                    " firmware on it first") if boot and not likely else ""
            raise UsageError("give --port (found " + (", ".join(p.device for p in likely)
                                                     or "no likely nRF Sniffer") + ")"
                             + (", none answered" if len(likely) > 1 else "") + hint)

    def show(p) -> None:
        if not args.print:
            return
        a = p.air
        if out.json:
            out.emit({"time": round(p.time, 6), "channel": a.channel, "rssi": a.rssi,
                      "protocol": p.protocol, "info": p.info, "hex": p.raw.hex()})
        else:
            print(f"{p.time:10.6f}  ch{a.channel:<2} {a.rssi:>4} dBm  {p.protocol:<8} {p.info}",
                  flush=True)
    if not out.json:
        print(f"Capturing on {port} for {args.duration:g} s…", file=sys.stderr, flush=True)
    result = await api.sniff(port, args.duration, args.out, follow=args.follow, on_packet=show)
    out.emit(result.to_dict() | {"event": "done"} if args.print and out.json else result.to_dict(),
             f"{result.packets} packets" + (f", {result.written} written to {result.path}"
                                            if result.path else "")
             + (f", followed {result.followed}" if result.followed else ""))
    return EXIT_OK


# -- parser -----------------------------------------------------------------
def _common() -> argparse.ArgumentParser:
    """Global options, accepted before or after the command."""
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                   help="JSON output (one object per line for streams)")
    p.add_argument("-v", "--verbose", action="count", default=argparse.SUPPRESS,
                   help="log to stderr (-v info, -vv debug)")
    p.add_argument("--timeout", type=float, default=argparse.SUPPRESS, metavar="S",
                   help="seconds to look for the device (default 10)")
    return p


def _device_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("device", nargs="?", metavar="DEVICE",
                   help="address (AA:BB:CC:DD:EE:FF) or exact name; or use the filters below")
    _filter_args(p)


def _filter_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("device filters")
    g.add_argument("--name", help="name contains this text (case-insensitive)")
    g.add_argument("--mfr", metavar="HEX", help="manufacturer data starts with these bytes, "
                   "e.g. 5900 (as on air) or 0059 (company ID as written); ? is any hex "
                   "digit and * any number of bytes, e.g. 0059??0B")
    g.add_argument("--service", metavar="UUID", help="advertises this service")
    g.add_argument("--min-rssi", type=int, metavar="DBM", help="ignore weaker devices")
    for ff in registry.filter_fields().values():  # from protocol extensions
        g.add_argument(f"--{ff.name}", metavar=ff.metavar, help=ff.help)


def build_parser() -> argparse.ArgumentParser:
    common = _common()
    parser = argparse.ArgumentParser(
        prog="bluebench-cli", parents=[common],
        description="BlueBench from the command line: scan, GATT, UART, macros, DFU, sniffer.",
        epilog="Exit codes: 0 ok, 1 failed, 2 usage, 3 device not found, "
               "4 Bluetooth unavailable, 130 interrupted.")
    parser.add_argument("--version", action="version", version=f"bluebench-cli {__version__}")
    # No set_defaults() for the global options: their actions are shared with every
    # subcommand (parents=), so it would reset values given before the command.
    sub = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    def command(name: str, fn, help: str, **kw) -> argparse.ArgumentParser:
        p = sub.add_parser(name, parents=[common], help=help, description=help, **kw)
        p.set_defaults(fn=fn)
        return p

    p = command("scan", cmd_scan, "list advertising devices (exit 3 if none matches)")
    p.add_argument("-t", "--duration", type=float, metavar="S",
                   help="scan time (default 5; with --first, the time limit)")
    p.add_argument("--first", action="store_true", help="stop at the first matching device")
    _filter_args(p)

    p = command("services", cmd_services, "connect and list services and characteristics")
    _device_args(p)

    fmt_help = "value format: " + ", ".join(FORMATS)
    p = command("read", cmd_read, "read a characteristic")
    _device_args(p)
    p.add_argument("char", metavar="CHAR", help="UUID (2a00 or full) or handle (0x0003)")
    p.add_argument("--as", dest="as_", choices=FORMATS, metavar="FMT", help=fmt_help)

    p = command("write", cmd_write, "write a characteristic")
    _device_args(p)
    p.add_argument("char", metavar="CHAR", help="UUID or handle")
    p.add_argument("value", metavar="VALUE", help="hex bytes (default) or per --as")
    p.add_argument("--as", dest="as_", choices=FORMATS, metavar="FMT", help=fmt_help)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--no-response", action="store_true", help="write command (no response)")
    g.add_argument("--with-response", action="store_true", help="force a write request")

    p = command("notify", cmd_notify, "print notifications/indications as they arrive")
    _device_args(p)
    p.add_argument("char", metavar="CHAR", help="UUID or handle")
    p.add_argument("--count", type=int, metavar="N", help="stop after N (exit 1 if fewer arrive)")
    p.add_argument("-t", "--duration", type=float, metavar="S", help="stop after S seconds")
    p.add_argument("--as", dest="as_", choices=FORMATS, metavar="FMT", help=fmt_help)

    p = command("pair", cmd_pair, "pair (bond) with a device")
    _device_args(p)
    p.add_argument("--passkey", help="passkey the device displays (asked for interactively "
                   "when omitted and needed)")
    p.add_argument("--reject", action="store_true", help="reject numeric comparison")

    p = command("unpair", cmd_unpair, "remove the bond with a device")
    _device_args(p)

    p = command("macro", None, "list or run macros")
    msub = p.add_subparsers(dest="macro_command", metavar="ACTION", required=True)
    m = msub.add_parser("list", parents=[common], help="list saved macros (or those in a file)")
    m.add_argument("--file", metavar="JSON", help="exported macros file instead of the library")
    m.set_defaults(fn=cmd_macro_list)
    m = msub.add_parser("run", parents=[common],
                        help="run a macro against a device (exit 1 if it fails)")
    _device_args(m)
    m.add_argument("macro", metavar="MACRO",
                   help="macro name in the library, or an exported .json file")
    m.add_argument("--pick", metavar="NAME", help="macro to run when the file holds several")
    m.add_argument("--repeat", type=int, default=1, metavar="N")
    m.add_argument("--keep-going", action="store_true", help="do not stop at the first failure")
    m.set_defaults(fn=cmd_macro_run)

    p = command("uart", cmd_uart, "Nordic UART: send text, print what the device sends back")
    _device_args(p)
    p.add_argument("--send", action="append", metavar="TEXT",
                   help="send this (repeatable); otherwise lines from stdin are sent")
    p.add_argument("--eol", choices=EOLS, default="lf", help="line ending added (default lf)")
    p.add_argument("--hex", action="store_true", help="send and show hex instead of text")
    p.add_argument("--until", metavar="TEXT",
                   help="stop when this text is received (exit 1 if it is not)")
    p.add_argument("-t", "--duration", type=float, metavar="S",
                   help="how long to listen after the last input (default 1 s; 10 s with --until)")

    p = command("dfu", cmd_dfu, "update firmware with a Nordic Secure DFU .zip")
    _device_args(p)
    p.add_argument("package", metavar="ZIP")
    p.add_argument("--prn", type=int, default=0, metavar="N",
                   help="packet receipt notification interval (0 = off)")

    p = command("sniff", None, "nRF Sniffer for Bluetooth LE (experimental)")
    ssub = p.add_subparsers(dest="sniff_command", metavar="ACTION", required=True)
    s = ssub.add_parser("ports", parents=[common], help="list serial ports (★ likely sniffers)")
    s.set_defaults(fn=cmd_sniff_ports)
    s = ssub.add_parser("capture", parents=[common], help="capture to a pcap file")
    s.add_argument("--port", help="serial port, e.g. COM5 (default: the only likely sniffer)")
    s.add_argument("-t", "--duration", type=float, required=True, metavar="S")
    s.add_argument("-o", "--out", metavar="PCAP", help="write a pcap file for Wireshark")
    s.add_argument("--follow", metavar="ADDRESS", help="follow this device into its connection")
    s.add_argument("--print", action="store_true", help="print every packet")
    s.set_defaults(fn=cmd_sniff_capture)
    for add_commands in registry.cli_extensions():  # protocol extensions
        add_commands(command, sys.modules[__name__])
    return parser


def _setup_logging(verbosity: int) -> None:
    # Without -v nothing is logged: a failure is reported once, by its error message.
    level = logging.CRITICAL + 1 if verbosity <= 0 else \
        logging.INFO if verbosity == 1 else logging.DEBUG
    log = logging.getLogger("bluebench")
    log.handlers[:] = []
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(level)
    log.propagate = False


def _exit_code(exc: BaseException) -> int:
    if isinstance(exc, (UsageError, ValueError)):
        return EXIT_USAGE
    if isinstance(exc, api.DeviceNotFound):
        return EXIT_NOT_FOUND
    if isinstance(exc, api.BluetoothUnavailable):
        return EXIT_NO_BLUETOOTH
    return EXIT_FAILED


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:      # --help / --version (0) or a usage error (2)
        return int(exc.code or 0)
    for name, default in GLOBAL_DEFAULTS.items():
        if not hasattr(args, name):
            setattr(args, name, default)
    _setup_logging(args.verbose)
    out = Out(args.json)
    try:
        return asyncio.run(args.fn(args, out))
    except KeyboardInterrupt:
        return EXIT_INTERRUPTED
    except (api.BlueBenchError, UsageError, ValueError, TimeoutError) as exc:
        code = _exit_code(exc)
        if args.json:
            out.emit({"ok": False, "error": str(exc), "type": type(exc).__name__,
                      "exit_code": code})
        print(f"error: {exc}", file=sys.stderr)
        return code


def run() -> None:
    """Console entry point."""
    code = main()
    try:
        from .ui.qt_compat import NEEDS_HARD_EXIT, hard_exit
    except ImportError:
        NEEDS_HARD_EXIT = False
    if NEEDS_HARD_EXIT:
        hard_exit(code)
    sys.exit(code)


if __name__ == "__main__":
    run()
