# PyInstaller spec for a windowed BlueBench.exe.
# Build with:  powershell -ExecutionPolicy Bypass -File build_exe.ps1
#
# BLUEBENCH_LAYOUT=onefile (default): one self-extracting exe. Easy to send, but it
#   unpacks ~80 MB to %TEMP% on every launch, which is slow and is the pattern
#   antivirus heuristics are most suspicious of.
# BLUEBENCH_LAYOUT=folder: BlueBench\BlueBench.exe plus an _internal folder.
#   Nothing is unpacked at runtime, so it starts in 1-2 s, and the AV scans
#   each DLL once and caches the verdict instead of rescanning every launch.
#   The folder also gets bluebench-cli.exe, the console command line, which
#   shares the same _internal folder.
import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

ROOT = SPECPATH + "/.."

# bleak picks its backend at runtime and the WinRT projections are imported
# dynamically, so PyInstaller's static analysis cannot see them.
# bluebench itself too: protocol extensions are imported by name (bluebench.extensions).
hiddenimports = collect_submodules("bleak") + collect_submodules("winrt") + collect_submodules("bluebench")

EXCLUDES = ["tkinter", "pytest", "PySide6.QtWebEngineCore", "PySide6.QtQml", "PySide6.QtQuick"]


def analysis(script):
    return Analysis(
        [SPECPATH + "/" + script],
        pathex=[ROOT + "/src"],
        datas=collect_data_files("bluebench") + copy_metadata("bleak"),
        hiddenimports=hiddenimports,
        excludes=EXCLUDES,
        noarchive=False,
    )


a = analysis("launcher.py")
# Qt pulls these in through plugins, but a widgets-only app never loads them.
# Dropping them roughly halves what the one-file exe must unpack at startup.
DROP = (
    "opengl32sw.dll",               # 20 MB software OpenGL fallback
    "qt6quick", "qt6qml", "qt6pdf", "qt6virtualkeyboard",
    "qdirect2d.dll",                # alternative platform plugin; qwindows is used
    "qpdf.dll", "qtiff.dll", "qwebp.dll", "qicns.dll", "qtga.dll", "qwbmp.dll",
    "qtvirtualkeyboardplugin.dll",
)


def _keep(entry):
    name = entry[0].replace("\\", "/").lower().rsplit("/", 1)[-1]
    return not any(name.startswith(d) or name == d for d in DROP)


def _trim(an):
    an.binaries = [b for b in an.binaries if _keep(b)]
    an.datas = [d for d in an.datas if _keep(d)]


_trim(a)

FOLDER = os.environ.get("BLUEBENCH_LAYOUT", "onefile").lower() == "folder"
exe_options = dict(
    name="BlueBench",
    console=False,
    icon=SPECPATH + "/icon.ico",
    upx=False,  # UPX-packed Qt DLLs trip antivirus heuristics more often
    version=SPECPATH + "/version_info.txt",
)

pyz = PYZ(a.pure)
if FOLDER:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, **exe_options)
    cli = analysis("cli_launcher.py")
    _trim(cli)
    cli_exe = EXE(PYZ(cli.pure), cli.scripts, [], exclude_binaries=True,
                  **(exe_options | dict(name="bluebench-cli", console=True)))
    # COLLECT keeps one copy of each DLL/data file the two programs share.
    coll = COLLECT(exe, cli_exe, a.binaries, a.datas, cli.binaries, cli.datas,
                   name="BlueBench", upx=False)
else:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], **exe_options)
