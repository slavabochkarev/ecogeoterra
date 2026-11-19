param(
    [int]$Port = 8000,
    [switch]$NoBrowser
)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3.12 -m venv .venv
    } else {
        & python -c "import sys; assert sys.version_info[:2] == (3, 12), 'Python 3.12 is required'"
        if ($LASTEXITCODE -ne 0) { throw "Install Python 3.12 and run start.ps1 again." }
        & python -m venv .venv
    }
    if ($LASTEXITCODE -ne 0) { throw "Could not create Python 3.12 virtual environment." }
}
& $venvPython -c "import sys; assert sys.version_info[:2] == (3, 12), 'Python 3.12 is required'"
if ($LASTEXITCODE -ne 0) { throw "The existing .venv must use Python 3.12." }
& $venvPython -m pip install --disable-pip-version-check -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Could not install dependencies. Check the internet connection." }
$url = "http://127.0.0.1:$Port"
Write-Host "EcoGeoTerra v0.2: $url"
Write-Host "Press Ctrl+C to stop. Local server: one worker only."
if (-not $NoBrowser) { Start-Process $url }
& $venvPython -m uvicorn app.main:app --host 127.0.0.1 --port $Port --workers 1
if ($LASTEXITCODE -ne 0) { throw "Server stopped with an error. Check whether the port is already in use." }
