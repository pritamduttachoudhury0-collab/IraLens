# One-command setup for IraLens on Windows (PowerShell).
#
#   .\scripts\setup.ps1              # runtime + dev tools into .\.venv
#   .\scripts\setup.ps1 -NoDev       # runtime only
#
# STATUS: UNTESTED. This script was written without access to a Windows machine.
# The POSIX script (scripts/setup.sh) is the tested path. Please report failures.
param([switch]$NoDev)

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
$VenvDir = if ($env:VENV_DIR) { $env:VENV_DIR } else { ".venv" }

$python = $null
foreach ($candidate in @("py -3", "python", "python3")) {
    try {
        $parts = $candidate -split " "
        $ok = & $parts[0] $parts[1..($parts.Length - 1)] -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) { $python = $parts; break }
    } catch { }
}
if (-not $python) { throw "Python 3.10 or newer is required (install from python.org or the Microsoft Store)." }

if (-not (Test-Path "$VenvDir\Scripts\python.exe")) {
    Write-Host "==> creating virtualenv in $VenvDir"
    & $python[0] $python[1..($python.Length - 1)] -m venv $VenvDir
}

$target = if ($NoDev) { "." } else { ".[dev]" }
Write-Host "==> installing iralens ($target) in editable mode"
& "$VenvDir\Scripts\python.exe" -m pip install --upgrade pip | Out-Null
& "$VenvDir\Scripts\python.exe" -m pip install -e $target

Write-Host "==> health check"
& "$VenvDir\Scripts\iralens.exe" --version
& "$VenvDir\Scripts\iralens.exe" --json doctor | Out-Null
Write-Host "doctor: ok"

Write-Host ""
Write-Host "Setup complete. Activate with: $VenvDir\Scripts\Activate.ps1"
Write-Host "Try it:  iralens search `"solar panel efficiency`""
Write-Host "Browser engine (optional): iralens install-engine"
