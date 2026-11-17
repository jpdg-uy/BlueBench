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
and the CLI, and tags `vX.Y.Z` (`-NoPublish` only builds and checks). Pushing the tag
publishes the release: the Release workflow builds everything again on a Windows runner and
publishes the GitHub release with the zip, the exe, the wheel and the three PDF manuals.

    git push origin vX.Y.Z

## Bitbucket mirror

The project can also live in a Bitbucket Cloud repository, for teams that work there. The
mirror has exactly the same commits: give `origin` a second push URL and every `git push`
goes to both places.

    git remote set-url --add --push origin https://github.com/jpdg-uy/BlueBench.git
    git remote set-url --add --push origin git@bitbucket.org:WORKSPACE/REPO.git

On Bitbucket, `bitbucket-pipelines.yml` runs the Linux tests on `main` and on pull requests.
When a tag `vX.Y.Z` arrives it waits for the GitHub release of that tag, checks the sha256
of every file and copies them to the repository's Downloads with the version in each name
(`packaging/bitbucket_release.py --from-github`). That step needs a repository access token
with the `repository:write` scope, saved as the secured repository variable
`BITBUCKET_ACCESS_TOKEN` (Repository settings → Access tokens, then Repository variables).
