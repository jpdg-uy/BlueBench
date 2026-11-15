"""Link-layer info: connection parameters, PHY and connection priority.

bleak does not expose these. Windows 11 does, through BluetoothLEDevice
(GetConnectionParameters / GetConnectionPhy / RequestPreferredConnectionParameters).
Apps cannot set exact intervals or choose a PHY on Windows; they can only ask
for one of three presets. Everything here is best-effort: on older Windows or
other platforms the functions return None and the UI shows the feature as
unavailable.
"""
from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .pairing import _requester

PRIORITIES = {
    "balanced": "Balanced",
    "throughput": "High throughput",
    "power": "Low power",
}


@dataclass(frozen=True)
class LinkInfo:
    interval_ms: float | None = None
    latency: int | None = None       # peripheral latency, in connection events
    timeout_ms: int | None = None    # supervision timeout
    tx_phy: str | None = None
    rx_phy: str | None = None

    def summary(self) -> str:
        parts = []
        if self.interval_ms is not None:
            parts.append(f"{self.interval_ms:g} ms")
        if self.tx_phy:
            phy = self.tx_phy if self.tx_phy == self.rx_phy or not self.rx_phy \
                else f"{self.tx_phy}/{self.rx_phy}"
            parts.append(f"{phy} PHY")
        return " · ".join(parts)

    def details(self) -> str:
        lines = []
        if self.interval_ms is not None:
            lines.append(f"Connection interval: {self.interval_ms:g} ms")
        if self.latency is not None:
            lines.append(f"Peripheral latency: {self.latency}")
        if self.timeout_ms is not None:
            lines.append(f"Supervision timeout: {self.timeout_ms} ms")
        if self.tx_phy:
            lines.append(f"PHY: TX {self.tx_phy}, RX {self.rx_phy or '?'}")
        return "\n".join(lines)


def _device(client: Any) -> Any:
    """The WinRT BluetoothLEDevice for a connected client, or None elsewhere."""
    return _requester(client) if sys.platform == "win32" else None


def phy_name(info: Any) -> str | None:
    if info is None:
        return None
    if info.is_uncoded2_m_phy:
        return "2M"
    if info.is_uncoded1_m_phy:
        return "1M"
    if info.is_coded_phy:
        return "Coded"
    return None


def read(client: Any) -> LinkInfo | None:
    """Current link parameters, or None if the platform cannot report them."""
    dev = _device(client)
    if dev is None:
        return None
    interval = latency = timeout = tx = rx = None
    try:
        p = dev.get_connection_parameters()
        if p is not None:
            interval = p.connection_interval * 1.25   # units of 1.25 ms
            latency = int(p.connection_latency)
            timeout = int(p.link_timeout) * 10          # units of 10 ms
    except Exception:
        pass  # Windows 10 lacks the API
    try:
        phy = dev.get_connection_phy()
        if phy is not None:
            tx, rx = phy_name(phy.transmit_info), phy_name(phy.receive_info)
    except Exception:
        pass
    if interval is None and tx is None:
        return None
    return LinkInfo(interval, latency, timeout, tx, rx)


def watch(client: Any, on_change: Callable[[], None]) -> Callable[[], None]:
    """Call on_change (from a WinRT thread) when parameters or PHY change.

    Returns a function that stops watching.
    """
    dev = _device(client)
    removers: list[Callable[[], None]] = []
    if dev is not None:
        for add, remove in (("add_connection_parameters_changed", "remove_connection_parameters_changed"),
                            ("add_connection_phy_changed", "remove_connection_phy_changed")):
            try:
                token = getattr(dev, add)(lambda *_: on_change())
            except Exception:
                continue
            removers.append(lambda r=getattr(dev, remove), t=token: r(t))

    def stop() -> None:
        for r in removers:
            try:
                r()
            except Exception:
                pass
        removers.clear()
    return stop


def supports_priority(client: Any) -> bool:
    dev = _device(client)
    return dev is not None and hasattr(dev, "request_preferred_connection_parameters")


def request_priority(client: Any, priority: str) -> Any:
    """Ask Windows for a connection-parameter preset.

    Returns the request object, which must be kept alive: closing it (or
    letting it be garbage collected) withdraws the preference. Raises
    RuntimeError with a readable reason on failure.
    """
    if priority not in PRIORITIES:
        raise ValueError(priority)
    dev = _device(client)
    if dev is None or not hasattr(dev, "request_preferred_connection_parameters"):
        raise RuntimeError("not supported on this system (needs Windows 11)")
    from winrt.windows.devices.bluetooth import (BluetoothLEPreferredConnectionParameters,
                                                 BluetoothLEPreferredConnectionParametersRequestStatus)
    preset = {
        "balanced": BluetoothLEPreferredConnectionParameters.balanced,
        "throughput": BluetoothLEPreferredConnectionParameters.throughput_optimized,
        "power": BluetoothLEPreferredConnectionParameters.power_optimized,
    }[priority]
    try:
        req = dev.request_preferred_connection_parameters(preset)
    except Exception as exc:
        raise RuntimeError(str(exc) or type(exc).__name__) from exc
    if req.status != BluetoothLEPreferredConnectionParametersRequestStatus.SUCCESS:
        name = req.status.name.replace("_", " ").lower()
        req.close()
        raise RuntimeError(name)
    return req
