"""Where BlueBench keeps user data (macros, custom UUID names)."""
from __future__ import annotations

import shutil
from pathlib import Path

DATA_DIR = Path.home() / ".bluebench"
LEGACY_DIR = Path.home() / ".nrfconnecter"   # the app was called nRFConnecter before 0.2


def migrate_legacy(data_dir: Path = DATA_DIR, legacy_dir: Path = LEGACY_DIR) -> bool:
    """Copy the old data folder once, so existing macros and names carry over.

    The old folder is left in place (an older build may still use it).
    Returns True if something was copied.
    """
    if data_dir.exists() or not legacy_dir.is_dir():
        return False
    try:
        shutil.copytree(legacy_dir, data_dir)
        return True
    except OSError:
        return False
