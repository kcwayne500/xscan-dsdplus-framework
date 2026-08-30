[CmdletBinding()]
param(
    [switch]$Clean,
    [switch]$BuildAndroid,
    [string]$PublicUrl = $env:XSCAN_PUBLIC_URL,
    [string]$PythonExe = ''
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

Push-Location $ProjectRoot
try {
    if (-not (Test-Path -LiteralPath $VenvPython)) {
        if ($PythonExe) {
            & $PythonExe -m venv (Join-Path $ProjectRoot '.venv')
        } else {
            py -3.12 -m venv (Join-Path $ProjectRoot '.venv')
        }
        if ($LASTEXITCODE -ne 0) { throw "Creating the Python environment failed with exit code $LASTEXITCODE." }
    }

    & $VenvPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "Updating pip failed with exit code $LASTEXITCODE." }
    & $VenvPython -m pip install -r (Join-Path $ProjectRoot 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw "Installing Python dependencies failed with exit code $LASTEXITCODE." }
    & $VenvPython -m pytest
    if ($LASTEXITCODE -ne 0) { throw "Python tests failed with exit code $LASTEXITCODE." }
    if ($BuildAndroid) {
        & (Join-Path $ProjectRoot 'build-android.ps1') -PublicUrl $PublicUrl
    }

    $Arguments = @('--noconfirm')
    if ($Clean) { $Arguments += '--clean' }
    $Arguments += (Join-Path $ProjectRoot 'XScanV2.spec')
    & $VenvPython -m PyInstaller @Arguments
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE." }
} finally {
    Pop-Location
}

Write-Host "XScan V2 build: $ProjectRoot\dist\XScanV2\XScanV2.exe"
