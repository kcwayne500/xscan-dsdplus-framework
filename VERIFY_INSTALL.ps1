[CmdletBinding()]
param(
    [string]$InstallDir = 'C:\DSDPlusFastlane',
    [switch]$Offline
)

$ErrorActionPreference = 'Continue'
$failures = [System.Collections.Generic.List[string]]::new()

function Test-RequiredFile {
    param([string]$RelativePath)
    $path = Join-Path $InstallDir $RelativePath
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        $failures.Add("Missing $path")
        Write-Host "[FAIL] $RelativePath" -ForegroundColor Red
    } else {
        Write-Host "[OK]   $RelativePath" -ForegroundColor Green
    }
}

foreach ($file in @(
    'DSDPlus.exe',
    'FMP24.exe',
    'START_EVERYTHING.cmd',
    'startup\start_scan_stack.py',
    'startup\stack_config.json',
    'mediamtx\mediamtx.exe',
    'scanner-recorder-repo\ffmpeg\ffmpeg.exe',
    'scanner-recorder-repo\scanner_gui_recorder.py',
    'scanner-recorder-repo\webui\index.html',
    'scanner-recorder-repo\webui\m\index.html',
    'scanner-recorder-repo\webui\m2\index.html',
    'scanner-recorder-repo\webui\radio\index.html',
    'scanner-recorder-repo\webui\mobile-player\index.html',
    'scanner-recorder-repo\webui\recordings\index.html',
    '.venv\Scripts\python.exe'
)) { Test-RequiredFile $file }

$python = Join-Path $InstallDir '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $python) {
    & $python -c 'import numpy, PySide6, sounddevice'
    if ($LASTEXITCODE -ne 0) {
        $failures.Add('Python dependency import failed.')
    } else {
        Write-Host '[OK]   Python imports' -ForegroundColor Green
    }
    & $python -m py_compile (Join-Path $InstallDir 'startup\start_scan_stack.py') (Join-Path $InstallDir 'scanner-recorder-repo\scanner_core.py') (Join-Path $InstallDir 'scanner-recorder-repo\scanner_gui_recorder.py') (Join-Path $InstallDir 'scanner-recorder-repo\scanner_web_server.py') (Join-Path $InstallDir 'scanner-recorder-repo\streaming_support.py')
    if ($LASTEXITCODE -ne 0) { $failures.Add('Python syntax verification failed.') }
}

if (-not $Offline) {
    foreach ($route in @('/', '/m/', '/m2/', '/radio/', '/mobile-player', '/recordings/', '/api/status')) {
        try {
            $response = Invoke-WebRequest -Uri "http://127.0.0.1:8890$route" -UseBasicParsing -TimeoutSec 4
            if ($response.StatusCode -ne 200) { throw "HTTP $($response.StatusCode)" }
            Write-Host "[OK]   HTTP $route" -ForegroundColor Green
        } catch {
            $failures.Add("Route $route failed: $($_.Exception.Message)")
            Write-Host "[FAIL] HTTP $route" -ForegroundColor Red
        }
    }
}

if ($failures.Count) {
    Write-Host ''
    $failures | ForEach-Object { Write-Host $_ -ForegroundColor Red }
    exit 1
}

Write-Host 'Installation verification passed.' -ForegroundColor Green
exit 0
