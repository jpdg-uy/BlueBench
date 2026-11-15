"""Nordic DFU .zip packages, as produced by ``nrfutil pkg generate``.

The zip holds a ``manifest.json`` plus, per image, an init packet (.dat,
signed metadata) and a firmware binary (.bin). A package can carry several
images; they are sent in the order the bootloader expects.
"""
from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path

# Bootloader/SoftDevice first: the application is validated against them.
IMAGE_ORDER = ("softdevice_bootloader", "softdevice", "bootloader", "application")
IMAGE_LABELS = {
    "softdevice_bootloader": "SoftDevice + bootloader",
    "softdevice": "SoftDevice",
    "bootloader": "Bootloader",
    "application": "Application",
}


class PackageError(ValueError):
    pass


@dataclass(frozen=True)
class DfuImage:
    kind: str
    init_packet: bytes
    firmware: bytes

    @property
    def label(self) -> str:
        return IMAGE_LABELS.get(self.kind, self.kind)


@dataclass(frozen=True)
class DfuPackage:
    path: Path
    images: list[DfuImage]

    @property
    def total_size(self) -> int:
        return sum(len(i.firmware) for i in self.images)

    def describe(self) -> str:
        return ", ".join(f"{i.label} ({len(i.firmware) / 1024:.1f} KB)" for i in self.images)


def load(path: str | Path) -> DfuPackage:
    path = Path(path)
    try:
        zf = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise PackageError(f"Not a DFU package (zip): {exc}") from None
    with zf:
        names = set(zf.namelist())
        if "manifest.json" not in names:
            raise PackageError("manifest.json is missing: not a Nordic DFU package")
        try:
            doc = json.loads(zf.read("manifest.json"))
        except ValueError as exc:
            raise PackageError(f"manifest.json is invalid: {exc}") from None
        if "files" in doc and "manifest" not in doc:
            raise PackageError(
                "This is an nRF Connect SDK / MCUboot package. It is updated over SMP (MCUmgr), "
                "not Nordic Secure DFU.")
        manifest = doc.get("manifest")
        if not isinstance(manifest, dict):
            raise PackageError("manifest.json has no 'manifest' section")
        images = []
        for kind in IMAGE_ORDER:
            entry = manifest.get(kind)
            if entry is None:
                continue
            try:
                dat, bin_ = entry["dat_file"], entry["bin_file"]
            except (KeyError, TypeError):
                raise PackageError(f"'{kind}' entry needs dat_file and bin_file") from None
            for name in (dat, bin_):
                if name not in names:
                    raise PackageError(f"{name} is listed in the manifest but missing from the zip")
            images.append(DfuImage(kind, zf.read(dat), zf.read(bin_)))
        unknown = set(manifest) - set(IMAGE_ORDER)
        if not images:
            extra = f" (unsupported entries: {', '.join(sorted(unknown))})" if unknown else ""
            raise PackageError("The package contains no firmware images" + extra)
        return DfuPackage(path, images)
