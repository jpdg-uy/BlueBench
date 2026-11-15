"""Log every notification of a characteristic to a CSV file until Ctrl+C.

    py examples/log_notifications.py --mfr 5900 2a37 hr.csv
    py examples/log_notifications.py C4:3A:7E:12:9B:01 6e400003-b5a3-f393-e0a9-e50e24dcca9e uart.csv

Reconnects when the device drops the link, so it can run for hours.
"""
import argparse
import asyncio
import csv
import logging
import time

from bluebench import api


async def log(args) -> None:
    with open(args.csv, "a", newline="") as f:
        out = csv.writer(f)
        if f.tell() == 0:
            out.writerow(["time", "iso_time", "hex"])
        while True:
            try:
                target = api.connect(args.device) if args.device else api.connect(mfr=args.mfr)
                async with target as dev:
                    print(f"Connected to {dev.name} ({dev.address}); logging {args.char}")
                    async with dev.notifications(args.char) as n:
                        async for value in n:          # ends if the device disconnects
                            now = time.time()
                            out.writerow([f"{now:.3f}", time.strftime("%Y-%m-%dT%H:%M:%S"),
                                          value.hex()])
                            f.flush()
                print("Disconnected; reconnecting…")
            except api.DeviceNotFound:
                print("Device not found; retrying in 5 s")
                await asyncio.sleep(5)
            except api.BlueBenchError as exc:
                print(f"{type(exc).__name__}: {exc}; retrying in 5 s")
                await asyncio.sleep(5)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("device", nargs="?", help="address or exact name (or use --mfr)")
    p.add_argument("char", help="characteristic UUID")
    p.add_argument("csv", help="output file (appended)")
    p.add_argument("--mfr", help="manufacturer data prefix, e.g. 5900")
    p.add_argument("-v", action="store_true", help="show BlueBench's log")
    args = p.parse_args()
    if not args.device and not args.mfr:
        p.error("give a device or --mfr")
    logging.basicConfig(level=logging.INFO if args.v else logging.WARNING)
    try:
        asyncio.run(log(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
