# Opens the control panel at http://127.0.0.1:8770 and hands you a Start button.
# The Windows twin of run.sh. Want the plain headless bridge instead? That is
# `zwiftbridge run`; this is `zwiftbridge ui`.
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $repo

$python = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "No .venv here. Create it first:"
    Write-Host "  py -3 -m venv .venv"
    Write-Host "  .venv\Scripts\pip install -e ."
    exit 1
}

# PYTHONPATH rather than an install, so a fresh clone runs without one.
$env:PYTHONPATH = if ($env:PYTHONPATH) { "$repo\src;$env:PYTHONPATH" } else { "$repo\src" }
& $python -m zwiftbridge ui @args
