"""Bench test for a real board with pytest and the blocking BlueBench API.

Run on Windows, with the board advertising:

    set BLUEBENCH_MFR=5900            (or BLUEBENCH_DEVICE=C4:3A:7E:12:9B:01)
    py -m pytest examples/test_device_pytest.py -v

Adapt the UUIDs and expected values to your firmware. The tests are skipped
when neither variable is set, so this file is harmless in a normal test run.
"""
import os

import pytest

from bluebench import api
from bluebench.api import sync as bb

DEVICE = os.environ.get("BLUEBENCH_DEVICE")
MFR = os.environ.get("BLUEBENCH_MFR")
NUS_RX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"

pytestmark = pytest.mark.skipif(not (DEVICE or MFR),
                                reason="set BLUEBENCH_DEVICE or BLUEBENCH_MFR")


@pytest.fixture(scope="module")
def dev():
    """One connection for the whole module (connecting takes 1-3 s on Windows)."""
    with (bb.connect(DEVICE) if DEVICE else bb.connect(mfr=MFR)) as d:
        yield d


def test_advertises_manufacturer_data():
    found = bb.find(timeout=10, address=DEVICE) if DEVICE else bb.find(timeout=10, mfr=MFR)
    assert found.manufacturer_data, "no manufacturer data in the advertisement"


def test_device_name(dev):
    assert dev.read("2a00").decode()      # Device Name is readable and not empty


def test_has_uart(dev):
    uuids = {c.uuid for s in dev.services() for c in s.characteristics}
    assert NUS_RX in uuids


def test_uart_echo(dev):
    with dev.uart() as uart:
        uart.send("ping\n")
        assert uart.readline(timeout=3).strip() == b"ping"   # adapt to your firmware's reply


def test_unknown_characteristic_is_reported(dev):
    with pytest.raises(api.GattError):
        dev.read("12345678-0000-1000-8000-00805f9b34fb")


@pytest.mark.skipif(not os.environ.get("BLUEBENCH_MACRO"), reason="set BLUEBENCH_MACRO")
def test_macro_from_the_app(dev):
    """A macro recorded in the BlueBench window (Macros tab), by name."""
    result = dev.run_macro(os.environ["BLUEBENCH_MACRO"])
    assert result.ok, "\n".join(f"{s.index + 1}. {s.description}: {s.error}"
                                for s in result.steps if not s.ok)
