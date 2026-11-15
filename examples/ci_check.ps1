# Bench / CI check with bluebench-cli: find the board, check its battery, run a macro.
# Usage:  powershell -ExecutionPolicy Bypass -File examples\ci_check.ps1 -Mfr 5900 -Macro "Self test"
# Exit code 0 when everything passes; the failing step's exit code otherwise.
param(
    [string]$Mfr = "5900",
    [string]$Macro = "",
    [int]$MinBattery = 20,
    [string]$Cli = "bluebench-cli"   # or the full path to BlueBench\bluebench-cli.exe
)
$ErrorActionPreference = "Stop"

function Invoke-Cli {
    $out = & $Cli --json @args
    $code = $LASTEXITCODE
    $json = if ($out) { $out | ConvertFrom-Json } else { $null }
    if ($code -ne 0) {
        $why = if ($json.error) { $json.error } else { "exit code $code" }
        Write-Host "FAIL: bluebench-cli $($args -join ' '): $why" -ForegroundColor Red
        exit $code
    }
    return $json
}

$dev = Invoke-Cli scan --first --mfr $Mfr -t 10
Write-Host "Found $($dev.name) $($dev.address) at $($dev.rssi) dBm"

$battery = Invoke-Cli read $dev.address 2a19 --as u8
if ([int]$battery.value -lt $MinBattery) {
    Write-Host "FAIL: battery $($battery.value)% < $MinBattery%" -ForegroundColor Red
    exit 1
}
Write-Host "Battery $($battery.value)%"

if ($Macro) {
    $result = Invoke-Cli macro run $dev.address $Macro
    Write-Host "Macro '$($result.macro)': $($result.summary) in $($result.seconds) s"
}
Write-Host "PASS" -ForegroundColor Green
