# Builds the Windows environment and runs the offline self-test.
# Creates .venv if it is missing (uv if installed, else py -3 -m venv) and
# installs this checkout editable. An existing .venv is reused, not replaced.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$uv = Get-Command uv -ErrorAction SilentlyContinue

if (-not (Test-Path $python)) {
    if ($uv) { uv venv .venv } else { py -3 -m venv .venv }
}
if ($uv) {
    uv pip install --python $python -e .
} else {
    & $python -m pip install --quiet --upgrade pip
    & $python -m pip install --quiet -e .
}
if ($LASTEXITCODE -ne 0) { throw "dependency install failed" }

& $python tests\selftest.py
if ($LASTEXITCODE -ne 0) { throw "selftest failed" }

Write-Host "Setup complete. Start the panel with .\run.cmd"
