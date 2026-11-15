"""PyInstaller entry point (it needs a script, not ``-m``)."""
from bluebench.__main__ import main

raise SystemExit(main())
