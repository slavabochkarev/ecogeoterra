param([switch]$SkipInstall)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
if ($env:OS -ne 'Windows_NT') { throw 'Build this distribution on Windows x64.' }
$buildPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $buildPython)) {
    & py -3.12 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 x64 is required.' }
}
& $buildPython -c "import sys, struct; assert sys.version_info[:2] == (3, 12) and struct.calcsize('P') == 8, 'Python 3.12 x64 is required'"
if ($LASTEXITCODE -ne 0) { throw 'Use Python 3.12 x64 for the build environment.' }
if (-not $SkipInstall) {
    & $buildPython -m pip install --disable-pip-version-check -r requirements-build.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
}
& $buildPython -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw 'Tests failed; build stopped.' }
& $buildPython -m PyInstaller --noconfirm --clean EcoburoGeo.spec
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }
& $buildPython scripts\package_windows.py
if ($LASTEXITCODE -ne 0) { throw 'Distribution validation/ZIP creation failed.' }
Write-Host 'Ready: dist\EcoburoGeo\EcoburoGeo.exe'
Write-Host 'ZIP: EcoburoGeo-v0.2-win64.zip'
