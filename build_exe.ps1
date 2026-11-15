# Builds the Windows app; no Python is needed on the target machine.
# Usage:  powershell -ExecutionPolicy Bypass -File build_exe.ps1 [-Layout Both|OneFile|Folder] [-Python 3.15]
#   OneFile -> dist\BlueBench.exe           (single file, slower start)
#   Folder  -> dist\BlueBench\ + dist\BlueBench-win64.zip
#              (unzip, run BlueBench.exe inside; fast start, friendlier to antivirus)
param(
    [ValidateSet("Both", "OneFile", "Folder")][string]$Layout = "Both",
    [string]$Python = "3.15"
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$venv = "build\venv"
if (-not (Test-Path "$venv\Scripts\python.exe")) {
    Write-Host "Creating build environment ($venv) with Python $Python..."
    py "-$Python" -m venv $venv
}
$py = "$venv\Scripts\python.exe"
& $py -m pip install --quiet --upgrade pip
& $py -m pip install --quiet . pyinstaller
if ($LASTEXITCODE) { throw "pip install failed" }

# User guide plus the CLI and Python API manuals, shipped next to the app.
$docs = "docs\BlueBench-Guia.pdf", "docs\BlueBench-CLI.pdf", "docs\BlueBench-API-Python.pdf"

function Build([string]$kind) {
    $env:BLUEBENCH_LAYOUT = $kind
    & $py -m PyInstaller --noconfirm --clean --distpath dist --workpath "build\pyinstaller-$kind" packaging\bluebench.spec
    if ($LASTEXITCODE) { throw "PyInstaller ($kind) failed" }
}

if ($Layout -in "Both", "OneFile") {
    Build "onefile"
    $exe = Get-Item dist\BlueBench.exe
    Write-Host ("Built {0} ({1:N1} MB)" -f $exe.FullName, ($exe.Length / 1MB))
    foreach ($d in $docs) { if (Test-Path $d) { Copy-Item $d dist\ } }
}
if ($Layout -in "Both", "Folder") {
    Build "folder"
    foreach ($d in $docs) { if (Test-Path $d) { Copy-Item $d dist\BlueBench\ } }
    $zip = "dist\BlueBench-win64.zip"
    # Python's zipfile, not Compress-Archive: PowerShell 5 writes "\" path
    # separators that some unzip tools mangle.
    & $py -c "import shutil; shutil.make_archive('dist/BlueBench-win64', 'zip', 'dist', 'BlueBench')"
    if ($LASTEXITCODE) { throw "zip failed" }
    $size = (Get-ChildItem dist\BlueBench -Recurse -File | Measure-Object Length -Sum).Sum
    Write-Host ("Built {0}\BlueBench.exe ({1:N1} MB folder)" -f (Resolve-Path dist\BlueBench), ($size / 1MB))
    Write-Host ("Zipped {0} ({1:N1} MB)" -f (Resolve-Path $zip), ((Get-Item $zip).Length / 1MB))
}
Remove-Item Env:BLUEBENCH_LAYOUT -ErrorAction SilentlyContinue
