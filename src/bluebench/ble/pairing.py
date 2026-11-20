"""Bonding (pair / unpair / bond state).

bleak's own ``pair()`` on Windows only accepts "Just Works" pairing and gives
no way to show or enter a passkey. Peripherals that use passkey entry or
numeric comparison (most nRF Connect SDK samples with security enabled) need
the full ceremony, so on Windows this module drives WinRT's custom pairing
directly and asks the UI through a ``Prompt`` callback. Other platforms fall
back to bleak.
"""
from __future__ import annotations

import asyncio
import re
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from bleak import BleakClient

# What the peripheral asks for, as passed to the prompt:
#   "confirm"      Just Works: no input, accept or decline
#   "display_pin"  show `pin`; the user types it on the peripheral
#   "provide_pin"  the peripheral shows a passkey; the user types it here
#   "confirm_pin"  numeric comparison: does the peripheral show `pin` too?
# The prompt returns the typed PIN for "provide_pin" (None to cancel) and
# True/False for the others.
Prompt = Callable[[str, str], Awaitable["str | bool | None"]]

# DevicePairingKinds flag values (Windows.Devices.Enumeration).
_KINDS = {0x1: "confirm", 0x2: "display_pin", 0x4: "provide_pin", 0x8: "confirm_pin"}
KIND_TEXT = {"confirm": "Just Works (no passkey)",
             "display_pin": "passkey entry: BlueBench shows it, type it on the device",
             "provide_pin": "passkey entry: the device shows it, type it here",
             "confirm_pin": "numeric comparison"}
_ALL_KINDS = 0x1 | 0x2 | 0x4 | 0x8

_STATUS_TEXT = {
    "AUTHENTICATION_TIMEOUT": "timed out waiting for the passkey",
    "AUTHENTICATION_FAILURE": "authentication failed (wrong passkey?)",
    "PAIRING_CANCELED": "cancelled",
    "REJECTED_BY_HANDLER": "cancelled",
    "PROTECTION_LEVEL_COULD_NOT_BE_MET": "the device cannot provide the required security",
    "NOT_READY_TO_PAIR": "the device is not ready to pair",
    "CONNECTION_REJECTED": "the device rejected the connection",
    "ACCESS_DENIED": "access denied",
    "OPERATION_ALREADY_IN_PROGRESS": "another pairing is already in progress",
}


class PairingError(Exception):
    pass


def _is_winrt() -> bool:
    return sys.platform == "win32"


def kind_name(flag: int) -> str:
    return _KINDS.get(int(flag), f"unsupported (0x{int(flag):X})")


def status_text(name: str) -> str:
    return _STATUS_TEXT.get(name, name.replace("_", " ").lower())


def address_to_int(address: str) -> int:
    return int(address.replace(":", "").replace("-", ""), 16)


def _requester(client: Any) -> Any:
    """The WinRT BluetoothLEDevice behind a connected bleak client (private API)."""
    return getattr(getattr(client, "_backend", None), "_requester", None)


# -- request bridging (WinRT thread -> asyncio loop) -------------------------
def handle_request(loop: asyncio.AbstractEventLoop, prompt: Prompt, args: Any,
                   log: Callable[[str, str], None] | None = None) -> None:
    """PairingRequested handler. WinRT calls it on a worker thread.

    A deferral keeps Windows waiting while the prompt runs on the UI loop.
    Not calling ``accept`` before completing the deferral declines pairing.
    """
    kind = kind_name(args.pairing_kind)
    pin = args.pin or ""
    deferral = args.get_deferral()

    async def answer() -> None:
        if log:  # here, on the loop: not on the WinRT thread
            log("info", f"The device asks for {KIND_TEXT.get(kind, kind)}")
        try:
            if kind not in _KINDS.values():
                if log:
                    log("error", f"Pairing method {kind} is not supported")
                return
            reply = await prompt(kind, pin)
            if kind == "provide_pin":
                if reply:
                    args.accept(str(reply))
            elif reply:
                args.accept()
        except Exception as exc:  # never leave Windows waiting on the deferral
            if log:
                log("error", f"Pairing prompt failed: {exc}")
        finally:
            deferral.complete()

    loop.call_soon_threadsafe(lambda: asyncio.ensure_future(answer()))


# -- public API ----------------------------------------------------------
# ATT errors that mean "this needs (working) encryption": insufficient
# authentication 0x05, insufficient encryption 0x0F, encryption key size 0x0C.
_AUTH_ERROR = re.compile(r"insufficient (authentication|encryption)|encryption key size"
                         r"|protocol error:? ?0x0?(5|c|f)\b|authentication failure"
                         r"|\bpin or key missing\b|\bkey missing\b", re.IGNORECASE)


def is_auth_error(error: BaseException | str) -> bool:
    """Whether an operation failed because the link is not (properly) encrypted."""
    return bool(_AUTH_ERROR.search(str(error)))


@dataclass(frozen=True)
class BondInfo:
    address: str   # identity address, as Windows stores the bond
    name: str
    id: str        # Windows device id, for unpair_bond()


_ID_ADDRESS = re.compile(r"([0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})$")


def address_from_device_id(device_id: str) -> str | None:
    """'BluetoothLE#BluetoothLE<adapter>-c4:3a:7e:12:9b:01' -> 'C4:3A:7E:12:9B:01'."""
    m = _ID_ADDRESS.search(device_id)
    return m.group(1).upper() if m else None


async def bonded_devices() -> list[BondInfo] | None:
    """All BLE devices bonded with this PC, or None if unknown.

    One OS query for the whole list, cheap enough to repeat every few seconds.
    """
    if not _is_winrt():
        return None
    try:
        from winrt.windows.devices.bluetooth import BluetoothLEDevice
        from winrt.windows.devices.enumeration import DeviceInformation
        selector = BluetoothLEDevice.get_device_selector_from_pairing_state(True)
        infos = await DeviceInformation.find_all_async(selector, None)
        return [BondInfo(a, i.name or "", i.id) for i in infos
                if (a := address_from_device_id(i.id))]
    except Exception:
        return None


async def bonded_addresses() -> set[str] | None:
    """Identity addresses of all bonded BLE devices, or None if unknown."""
    bonds = await bonded_devices()
    return None if bonds is None else {b.address for b in bonds}


def identity_address(client: Any) -> str | None:
    """The address Windows uses for a connected device: its identity address once
    bonded (the advertised one may be a changing RPA)."""
    req = _requester(client)
    try:
        return address_from_device_id(req.device_information.id) if req is not None else None
    except Exception:
        return None


async def unpair_bond(device_id: str) -> None:
    """Remove one bond listed by bonded_devices()."""
    from winrt.windows.devices.enumeration import DeviceInformation, DeviceUnpairingResultStatus
    try:
        info = await DeviceInformation.create_from_id_async(device_id)
        result = await info.pairing.unpair_async()
    except Exception as exc:
        raise PairingError(str(exc) or type(exc).__name__) from exc
    if result.status not in (DeviceUnpairingResultStatus.UNPAIRED,
                             DeviceUnpairingResultStatus.ALREADY_UNPAIRED):
        raise PairingError(result.status.name.replace("_", " ").lower())



async def bond_state(client: Any | None, address: str) -> bool | None:
    """True/False if the OS knows, None if this platform cannot tell."""
    if not _is_winrt():
        return None
    try:
        from winrt.windows.devices.bluetooth import BluetoothLEDevice
        from winrt.windows.devices.enumeration import DeviceInformation
        req = _requester(client)
        if req is not None:
            # A fresh DeviceInformation: the requester's copy is not updated.
            info = await DeviceInformation.create_from_id_async(req.device_information.id)
            return bool(info.pairing.is_paired)
        dev = await BluetoothLEDevice.from_bluetooth_address_async(address_to_int(address))
        if dev is None:
            return None
        try:
            return bool(dev.device_information.pairing.is_paired)
        finally:
            dev.close()
    except Exception:
        return None


async def pair(client: Any, prompt: Prompt,
               log: Callable[[str, str], None] | None = None) -> str:
    """Pair (bond) with a connected device. Returns the protection level used."""
    if not _is_winrt():
        try:
            await client.pair()
        except Exception as exc:
            raise PairingError(str(exc) or type(exc).__name__) from exc
        return "unknown"

    from winrt.windows.devices.enumeration import (DeviceInformation, DevicePairingKinds,
                                                   DevicePairingProtectionLevel,
                                                   DevicePairingResultStatus)
    req = _requester(client)
    if req is None:
        raise PairingError("not connected")
    info = await DeviceInformation.create_from_id_async(req.device_information.id)
    if info.pairing.is_paired:
        return "already bonded"
    if not info.pairing.can_pair:
        raise PairingError("the device does not support pairing")

    loop = asyncio.get_running_loop()
    custom = info.pairing.custom
    token = custom.add_pairing_requested(
        lambda _sender, args: handle_request(loop, prompt, args, log))
    try:
        result = None
        # Ask for MITM protection first; Just Works devices answer
        # "protection level could not be met" and are retried with
        # encryption only (same strategy as bleak).
        for level in (DevicePairingProtectionLevel.ENCRYPTION_AND_AUTHENTICATION,
                      DevicePairingProtectionLevel.ENCRYPTION):
            result = await custom.pair_async(DevicePairingKinds(_ALL_KINDS), level)
            if result.status != DevicePairingResultStatus.PROTECTION_LEVEL_COULD_NOT_BE_MET:
                break
    except Exception as exc:
        raise PairingError(str(exc) or type(exc).__name__) from exc
    finally:
        custom.remove_pairing_requested(token)

    if result.status not in (DevicePairingResultStatus.PAIRED,
                             DevicePairingResultStatus.ALREADY_PAIRED):
        raise PairingError(status_text(result.status.name))
    return result.protection_level_used.name.replace("_", " ").lower()


async def unpair(client: Any | None, address: str, name: str | None = None) -> None:
    """Remove the bond. Works whether or not the device is connected.

    ``address`` is best the identity address; when Windows does not know it (an
    RPA), the bond is looked up by address, then by ``name`` if only one matches.
    On Windows this also drops the connection.
    """
    if not _is_winrt():
        try:
            await (client or BleakClient(address)).unpair()
        except Exception as exc:
            raise PairingError(str(exc) or type(exc).__name__) from exc
        return

    from winrt.windows.devices.bluetooth import BluetoothLEDevice
    from winrt.windows.devices.enumeration import DeviceInformation, DeviceUnpairingResultStatus
    try:
        req = _requester(client)
        if req is not None:
            info = await DeviceInformation.create_from_id_async(req.device_information.id)
            result = await info.pairing.unpair_async()
        else:
            # Windows lists bonds by identity address; a device seen only with an
            # RPA is found by its name when exactly one bond has it.
            bonds = await bonded_devices() or []
            match = [b for b in bonds if b.address == address.upper()]
            if not match and name:
                match = [b for b in bonds if b.name == name]
            if len(match) > 1:
                raise PairingError(f"{len(match)} bonds are named {name!r}: remove the right "
                                   "one from the list of bonds")
            if match:
                await unpair_bond(match[0].id)
                return
            dev = await BluetoothLEDevice.from_bluetooth_address_async(address_to_int(address))
            if dev is None:
                raise PairingError("Windows does not know this device")
            try:
                result = await dev.device_information.pairing.unpair_async()
            finally:
                dev.close()
    except PairingError:
        raise
    except Exception as exc:
        raise PairingError(str(exc) or type(exc).__name__) from exc
    if result.status not in (DeviceUnpairingResultStatus.UNPAIRED,
                             DeviceUnpairingResultStatus.ALREADY_UNPAIRED):
        raise PairingError(result.status.name.replace("_", " ").lower())
