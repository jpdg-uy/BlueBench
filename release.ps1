# Builds and checks a release on this PC, then tags it. Publishing happens when the tag is
# pushed: .github/workflows/release.yml builds on a Windows runner and publishes the GitHub
# release, and bitbucket-pipelines.yml copies that release to the Bitbucket mirror.
# Usage:  powershell -ExecutionPolicy Bypass -File release.ps1 [-NoPublish] [-SkipBuild]
#   Runs the tests, builds dist\ (build_exe.ps1 -Layout Both), smoke-tests the frozen app
#   and bluebench-cli.exe, then creates the tag v<__version__> on HEAD. Push it with
#   "git push origin v<__version__>" from the shell that has the keys of every push URL.
param(
    [switch]$NoPublish,   # build and check only, no tag
    [switch]$SkipBuild    # reuse what is already in dist\
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$version = (Select-String -Path src\bluebench\__init__.py -Pattern '__version__ = "(.+)"').Matches[0].Groups[1].Value
$tag = "v$version"
Write-Host "Releasing BlueBench $tag"

# The release must match a commit that is already pushed.
if (git status --porcelain --untracked-files=no) { throw "Uncommitted changes: commit or stash them first" }
# (origin refs are as fresh as the last push/fetch; no fetch here to avoid credential prompts)
$head = git rev-parse HEAD
if (-not (git branch -r --contains $head)) { throw "HEAD $head is not pushed to origin" }
if (git tag --list $tag) {
    if ((git rev-list -n 1 $tag) -ne $head) { throw "Tag $tag already exists on another commit: bump __version__" }
}

$py = "build\venv\Scripts\python.exe"
if (-not $SkipBuild) {
    ./build_exe.ps1 -Layout Both
    & $py -m pip install --quiet "pytest>=8" "pytest-asyncio>=0.23"
    & $py -m pytest -q -p no:cacheprovider tests
    if ($LASTEXITCODE) { throw "Tests failed" }
}

# Python wheel of the library and CLI: pip install bluebench-<version>-py3-none-any.whl
Remove-Item dist\bluebench-*.whl -ErrorAction SilentlyContinue
# setuptools reuses build\lib, so stale modules from older builds would end up in the wheel.
Remove-Item build\lib, build\bdist.* -Recurse -Force -ErrorAction SilentlyContinue
& $py -m pip wheel --quiet --no-deps --wheel-dir dist .
if ($LASTEXITCODE) { throw "wheel build failed" }
$wheel = "dist\bluebench-$version-py3-none-any.whl"
$stray = & $py -c "import sys, zipfile; print('\n'.join(sorted({n.split('/')[0] for n in zipfile.ZipFile(sys.argv[1]).namelist()} - {'bluebench', 'bluebench-$version.dist-info'})))" $wheel
if ($stray) { throw "Unexpected content in the wheel: $stray" }

$assets = "dist\BlueBench-win64.zip", "dist\BlueBench.exe", $wheel,
          "docs\BlueBench-Guia.pdf", "docs\BlueBench-CLI.pdf", "docs\BlueBench-API-Python.pdf"
foreach ($a in $assets) { if (-not (Test-Path $a)) { throw "Missing $a" } }

# The manuals inside the zip must be the current ones in docs\ (a zip left over from an
# earlier build would ship outdated PDFs).
$stale = & $py -c "import hashlib, sys, zipfile; z = zipfile.ZipFile(sys.argv[1]); print(' '.join(n for n in ('BlueBench-Guia.pdf', 'BlueBench-CLI.pdf', 'BlueBench-API-Python.pdf') if hashlib.sha256(z.read('BlueBench/' + n)).digest() != hashlib.sha256(open('docs/' + n, 'rb').read()).digest()))" dist\BlueBench-win64.zip
if ($LASTEXITCODE -or $stale) { throw "The zip has outdated manuals ($stale): rebuild without -SkipBuild" }

# Smoke test: the frozen app starts and reports its bundled modules.
$report = Join-Path $env:TEMP "bluebench-selftest.json"
Remove-Item $report -ErrorAction SilentlyContinue
Start-Process dist\BlueBench\BlueBench.exe -ArgumentList '--self-test', "`"$report`"" -Wait
$r = Get-Content $report -Raw | ConvertFrom-Json
if (-not $r.frozen -or -not $r.pyserial -or $r.bleak -eq '?' -or $r.version -ne $version) {
    throw "Frozen app self-test failed: $(Get-Content $report -Raw)"
}
Write-Host "Self-test ok (version $($r.version), bleak $($r.bleak), pyserial $($r.pyserial))"

# The command line ships in the folder build: it must start and answer.
$cli = "dist\BlueBench\bluebench-cli.exe"
$cliVersion = & $cli --version
if ($LASTEXITCODE -or $cliVersion -ne "bluebench-cli $version") { throw "bluebench-cli --version failed: $cliVersion" }
$ports = & $cli --json sniff ports
if ($LASTEXITCODE) { throw "bluebench-cli sniff ports failed: $ports" }
$null = $ports | ConvertFrom-Json
Write-Host "CLI ok ($cliVersion)"

if ($NoPublish) { Write-Host "Built and checked; not tagged (-NoPublish)."; exit 0 }

if (-not (git tag --list $tag)) {
    git tag -a $tag -m "BlueBench $tag"
    if ($LASTEXITCODE) { throw "Creating tag $tag failed" }
}
Write-Host "Tagged $tag. Publish it with:  git push origin $tag"
