param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8000,
    [switch]$NoBrowser,
    [switch]$InstallDependencies
)
$ErrorActionPreference = "Stop"
$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
Push-Location -LiteralPath $PSScriptRoot
$defaultRadiusFlags = @()
try {
    if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) {
        throw "Port $Port is already in use. Stop your EcoGeoTerra instance or choose another port."
    }
    $firstRun = -not (Test-Path -LiteralPath $venvPython)
    if ($firstRun) {
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
    if ($firstRun -or $InstallDependencies) {
        & $venvPython -m pip install --disable-pip-version-check -r requirements.txt
        if ($LASTEXITCODE -ne 0) { throw "Could not install dependencies. Check the internet connection." }
    }

    foreach ($flag in 'NSPD_RESEARCH_ENABLE_1000', 'NSPD_RESEARCH_ENABLE_2000') {
        if (-not (Test-Path "Env:$flag")) {
            Set-Item -LiteralPath "Env:$flag" -Value '1'
            $defaultRadiusFlags += $flag
        }
    }

    $url = "http://127.0.0.1:$Port/"
    Write-Host "EcoGeoTerra: $url"
    Write-Host "Press Ctrl+C to stop. Local server: one worker only."
    if (-not $NoBrowser) { Start-Process $url }
    & $venvPython -B -m uvicorn app.main:app --host 127.0.0.1 --port $Port --workers 1
    if ($LASTEXITCODE -ne 0) { throw "Server stopped with an error. Check the port, configuration and dependencies." }
} finally {
    foreach ($flag in $defaultRadiusFlags) { Remove-Item -LiteralPath "Env:$flag" }
    Pop-Location
}
