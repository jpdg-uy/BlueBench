# Contributing to BlueBench

Thanks for helping! Issues and pull requests are welcome, in English or Spanish.

## Reporting bugs and asking for features

Use [New issue](https://github.com/jpdg-uy/BlueBench/issues/new/choose) and pick the form.
For bugs, the most useful things are:

- the BlueBench version (window title) and Windows version,
- the Bluetooth adapter and the device you were talking to,
- the exported log (Log panel → **Export…**), and for sniffer problems the exported `.pcap`,
- the output of `BlueBench.exe --self-test` if Bluetooth does not seem to work at all.

## Development setup

Bluetooth needs Windows (WSL2 has no Bluetooth), but the tests run anywhere.

```
py -m pip install -e .[dev]
py -m bluebench          # run the app
py -m pytest -q          # run the tests (no radio needed: BLE is faked)
```

Layout: `src/bluebench/ble` (BLE: scanner, connection, pairing), `core` (log, macros),
`tools` (NUS, DFU), `sniffer` (Traffic tab back end), `ui` (Qt widgets). Every BLE operation
goes through `ble/connection.py:DeviceSession`.

## Pull requests

- Open an issue first for larger changes, so we agree on the approach.
- Keep the UI responsive: BLE calls are `async` on the qasync loop; never block the Qt thread.
- Add or update tests (`tests/`, see `fakes.py`, `fake_dfu.py`, `fake_air.py` for the fakes).
- `pytest` must pass; CI runs it on Linux (PySide6 6.9 and latest) and Windows.
- Write `Fixes #123` in the description to close the issue on merge.
- If you change the UI, update the README and, if you can, the user guide
  (`docs/guide`, see the README for how to regenerate it).

## Releases

Bump `__version__` in `src/bluebench/__init__.py` (plus `pyproject.toml` and
`packaging/version_info.txt`), commit and push. Then, on Windows:

    powershell -ExecutionPolicy Bypass -File release.ps1

It runs the tests, builds the exe, the zip and the Python wheel, smoke-tests the frozen app
and the CLI, tags `vX.Y.Z` and publishes the GitHub release with `gh` (`-NoPublish` only
builds and checks, `-Draft` publishes a draft).

Pushing the tag is also enough on its own: the Release workflow builds everything on a
Windows runner and publishes the release.
