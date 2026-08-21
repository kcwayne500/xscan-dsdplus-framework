@echo off
setlocal
title DSDPlus Scanner System Launcher

rem This is a CMD/PowerShell hybrid so it remains a single, double-clickable file.
set "DSDPLUS_LAUNCHER_FILE=%~f0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "$launcherFile=$env:DSDPLUS_LAUNCHER_FILE; $raw=[IO.File]::ReadAllText($launcherFile); $marker='# POWERSHELL_PAYLOAD'; $at=$raw.LastIndexOf($marker); if($at -lt 0){throw 'Launcher payload was not found.'}; Invoke-Expression $raw.Substring($at+$marker.Length)"
set "launcherExit=%ERRORLEVEL%"

if not "%launcherExit%"=="0" (
    echo.
    echo Startup did not complete successfully. Review the messages above.
    pause
)
exit /b %launcherExit%

# POWERSHELL_PAYLOAD
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent ([IO.Path]::GetFullPath($launcherFile))
$startupDir = Join-Path $root 'startup'
$launcherLog = Join-Path $startupDir 'launch_all.log'
$installConfigPath = Join-Path $root 'install_config.json'
$publicHost = 'xscan.cc-group.org'
if (Test-Path -LiteralPath $installConfigPath -PathType Leaf) {
    try {
        $installConfig = Get-Content -LiteralPath $installConfigPath -Raw | ConvertFrom-Json
        if ($installConfig.public_host) { $publicHost = [string]$installConfig.public_host }
    } catch {}
}
$env:XSCAN_HOME = $root
$env:XSCAN_PUBLIC_HOST = $publicHost
$stackScript = Join-Path $startupDir 'start_scan_stack.py'
$recorderDir = Join-Path $root 'scanner-recorder-repo'
$recorderScript = Join-Path $recorderDir 'scanner_gui_recorder.py'
$apiUrl = 'http://127.0.0.1:8890/api/status'
$webRoutes = @('/', '/m/', '/m2/', '/radio/', '/mobile-player', '/recordings/')
$caddyExe = 'C:\Caddy\caddy.exe'
$caddyConfig = 'C:\Caddy\Caddyfile'

function Write-LaunchLog {
    param([string]$Message, [ConsoleColor]$Color = [ConsoleColor]::Gray)
    $timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Write-Host $Message -ForegroundColor $Color
    try { Add-Content -LiteralPath $launcherLog -Value "[$timestamp] $Message" -Encoding UTF8 } catch {}
}

function Get-MatchingProcess {
    param([string]$ImageName, [string]$CommandFragment)
    @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        (!$ImageName -or $_.Name -ieq $ImageName) -and
        (!$CommandFragment -or $_.CommandLine -like "*$CommandFragment*")
    })
}

function Test-NamedProcess {
    param([string]$Name)
    return $null -ne (Get-Process -Name $Name -ErrorAction SilentlyContinue | Select-Object -First 1)
}

function Get-ScannerStatus {
    try {
        return Invoke-RestMethod -Uri $apiUrl -TimeoutSec 2 -ErrorAction Stop
    } catch {
        return $null
    }
}

function Get-RecorderProcesses {
    @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        ($_.Name -match '^python(w)?(\d+\.\d+)?\.exe$' -and $_.CommandLine -and $_.CommandLine -like '*scanner_gui_recorder.py*') -or
        $_.Name -ieq 'DSDPlusScannerRecorder.exe'
    })
}

function Get-ControllerProcesses {
    @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -match '^python(w)?(\d+\.\d+)?\.exe$' -and
        $_.CommandLine -and $_.CommandLine -like '*start_scan_stack.py*'
    })
}

function Test-WebRoutes {
    $passed = 0
    foreach ($route in $webRoutes) {
        try {
            $response = Invoke-WebRequest -Uri ("http://127.0.0.1:8890$route") -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
            if ($response.StatusCode -eq 200) { $passed++ }
        } catch {}
    }
    return [pscustomobject]@{ Passed = $passed; Total = $webRoutes.Count; Healthy = $passed -eq $webRoutes.Count }
}

function Wait-ForCondition {
    param([scriptblock]$Condition, [int]$TimeoutSeconds)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        if (& $Condition) { return $true }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)
    return $false
}

function Show-Check {
    param([bool]$Passed, [string]$Label, [string]$Detail = '')
    $suffix = if ($Detail) { " - $Detail" } else { '' }
    if ($Passed) {
        Write-LaunchLog "[OK]   $Label$suffix" Green
    } else {
        Write-LaunchLog "[FAIL] $Label$suffix" Red
    }
}

try {
    Set-Location -LiteralPath $root
    New-Item -ItemType Directory -Path $startupDir -Force | Out-Null
    Write-LaunchLog ''
    Write-LaunchLog 'Starting the complete DSDPlus scanner system...' Cyan

    $requiredFiles = @(
        (Join-Path $root 'DSDPlus.exe'),
        (Join-Path $root 'FMP24.exe'),
        $stackScript,
        $recorderScript,
        (Join-Path $recorderDir 'ffmpeg\ffmpeg.exe'),
        (Join-Path $root 'mediamtx\mediamtx.exe'),
        (Join-Path $recorderDir 'scanner_gui_recorder_settings.json'),
        (Join-Path $recorderDir 'webui\index.html'),
        (Join-Path $recorderDir 'webui\m\index.html'),
        (Join-Path $recorderDir 'webui\m2\index.html'),
        (Join-Path $recorderDir 'webui\radio\index.html'),
        (Join-Path $recorderDir 'webui\mobile-player\index.html'),
        (Join-Path $recorderDir 'webui\recordings\index.html'),
        $caddyExe,
        $caddyConfig
    )
    $missing = @($requiredFiles | Where-Object { -not (Test-Path -LiteralPath $_ -PathType Leaf) })
    if ($missing.Count -gt 0) {
        foreach ($item in $missing) { Write-LaunchLog "[MISSING] $item" Red }
        throw 'One or more required scanner files are missing.'
    }

    $python = Join-Path $root '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        $python = (& python.exe -c 'import sys; print(sys.executable)' 2>$null | Select-Object -Last 1)
        if (-not $python) { throw 'Python 3 was not found. Run INSTALL.cmd first.' }
        $python = $python.Trim()
    }
    & $python -c 'import numpy, PySide6, sounddevice' 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'Python is missing one or more required packages: numpy, PySide6, sounddevice.' }
    $pythonw = Join-Path (Split-Path -Parent $python) 'pythonw.exe'
    if (-not (Test-Path -LiteralPath $pythonw -PathType Leaf)) { $pythonw = $python }
    Show-Check $true 'Prerequisites' "Python $((& $python --version 2>&1) -replace '^Python\s+','')"

    $caddyService = Get-Service -Name 'Caddy' -ErrorAction SilentlyContinue
    if ($null -ne $caddyService -and $caddyService.Status -ne 'Running') {
        Write-LaunchLog 'Starting the Caddy public HTTPS proxy...'
        try { Start-Service -Name 'Caddy' -ErrorAction Stop } catch {
            Write-LaunchLog "Caddy could not be started: $($_.Exception.Message)" Yellow
        }
        $caddyService = Get-Service -Name 'Caddy' -ErrorAction SilentlyContinue
    }

    $controllerStarted = $false
    $controller = @(Get-ControllerProcesses)
    if ($controller.Count -eq 0) {
        Write-LaunchLog 'Starting the DSDPlus/FMP24 scan controller...'
        Start-Process -FilePath $pythonw -ArgumentList ('"{0}"' -f $stackScript) -WorkingDirectory $startupDir | Out-Null
        $controllerStarted = $true
    } else {
        Write-LaunchLog 'The DSDPlus/FMP24 scan controller is already running; keeping it.' DarkGray
    }

    $radioStarted = Wait-ForCondition -TimeoutSeconds 20 -Condition {
        (Test-NamedProcess 'DSDPlus') -and (Test-NamedProcess 'FMP24')
    }

    # Replacing an orphaned radio stack can briefly reset the VB-Cable endpoint.
    # Give the recorder time to report that condition before deciding to keep it.
    if ($controllerStarted -and $radioStarted) { Start-Sleep -Seconds 3 }

    $status = Get-ScannerStatus
    $expectedRecorderRunning = @(Get-RecorderProcesses | Where-Object {
        $_.CommandLine -and $_.CommandLine -like "*$recorderScript*"
    }).Count -gt 0
    $recorderIsHealthy = $null -ne $status -and
        $expectedRecorderRunning -and
        $status.status -in @('RUNNING', 'RECORDING') -and
        (!$status.streaming_enabled -or $status.stream_status -eq 'LIVE')
    if (-not $recorderIsHealthy) {
        $recorderProcesses = @(Get-RecorderProcesses)

        if ($recorderProcesses.Count -gt 0) {
            Write-LaunchLog 'Restarting an unresponsive scanner recorder instance...' Yellow
            $recorderPids = @($recorderProcesses | Select-Object -ExpandProperty ProcessId -Unique)
            # Force-stopping a GUI process can leave its MediaMTX/FFmpeg children
            # behind, so stop only children owned by these recorder instances first.
            Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
                $_.ParentProcessId -in $recorderPids -and $_.Name -in @('mediamtx.exe', 'ffmpeg.exe')
            } | ForEach-Object {
                Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
            }
            $recorderPids | ForEach-Object {
                Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue
            }
            Start-Sleep -Milliseconds 800
        } else {
            Write-LaunchLog 'Starting the scanner recorder, web player, and live stream...'
        }

        Start-Process -FilePath $pythonw -ArgumentList ('"{0}"' -f $recorderScript) -WorkingDirectory $recorderDir | Out-Null
    } else {
        Write-LaunchLog 'The scanner recorder and web API are already running; keeping them.' DarkGray
    }

    $scannerReady = Wait-ForCondition -TimeoutSeconds 30 -Condition {
        $script:scannerState = Get-ScannerStatus
        $null -ne $script:scannerState -and $script:scannerState.status -in @('RUNNING', 'RECORDING')
    }
    $streamReady = Wait-ForCondition -TimeoutSeconds 15 -Condition {
        $script:scannerState = Get-ScannerStatus
        $null -ne $script:scannerState -and $script:scannerState.stream_status -eq 'LIVE'
    }

    $controllerOk = @(Get-ControllerProcesses).Count -gt 0
    $dsdOk = Test-NamedProcess 'DSDPlus'
    $fmpOk = Test-NamedProcess 'FMP24'
    $mediaMtxOk = Test-NamedProcess 'mediamtx'
    $ffmpegOk = Test-NamedProcess 'ffmpeg'
    $status = Get-ScannerStatus
    $webOk = $null -ne $status
    $routeHealth = if ($webOk) { Test-WebRoutes } else { [pscustomobject]@{ Passed = 0; Total = $webRoutes.Count; Healthy = $false } }
    $monitorOk = $webOk -and $status.status -in @('RUNNING', 'RECORDING')
    $streamOk = $webOk -and $status.stream_status -eq 'LIVE' -and $mediaMtxOk -and $ffmpegOk
    $caddyOk = $null -ne $caddyService -and $caddyService.Status -eq 'Running'

    Write-LaunchLog ''
    Write-LaunchLog 'System health check' Cyan
    Show-Check $controllerOk 'Scan controller'
    Show-Check $dsdOk 'DSDPlus decoder'
    Show-Check $fmpOk 'FMP24 tuner'
    Show-Check $webOk 'Scanner app and web API' 'port 8890'
    Show-Check $routeHealth.Healthy 'Web app variants' "$($routeHealth.Passed)/$($routeHealth.Total) routes"
    $monitorDetail = if ($webOk) { [string]$status.status } else { 'not available' }
    Show-Check $monitorOk 'Audio monitoring and recording' $monitorDetail
    $streamDetail = if ($webOk) { [string]$status.stream_status } else { 'not available' }
    Show-Check $streamOk 'MediaMTX/FFmpeg live stream' $streamDetail
    Show-Check $caddyOk 'Caddy public HTTPS proxy' $publicHost

    $allOk = $controllerOk -and $dsdOk -and $fmpOk -and $webOk -and $routeHealth.Healthy -and $monitorOk -and $streamOk -and $caddyOk
    if (-not $allOk) {
        Write-LaunchLog ''
        Write-LaunchLog "Startup is incomplete. Details are in $launcherLog" Yellow
        Write-LaunchLog "DSDPlus runtime log: $(Join-Path $startupDir 'dsdplus_runtime.log')" Yellow
        exit 1
    }

    $playerUrl = if ($status.web_player_url) { [string]$status.web_player_url } else { 'http://127.0.0.1:8890/' }
    Write-LaunchLog ''
    Write-LaunchLog 'Everything is running.' Green
    Write-LaunchLog "Web player: $playerUrl" Cyan
    Write-LaunchLog "Local player: http://127.0.0.1:8890/" Cyan
    Write-LaunchLog 'This window will close in 5 seconds. Run this file again anytime to re-check the system.' DarkGray
    Start-Sleep -Seconds 5
    exit 0
} catch {
    Write-LaunchLog ''
    Write-LaunchLog "Startup error: $($_.Exception.Message)" Red
    Write-LaunchLog "Launcher log: $launcherLog" Yellow
    exit 1
}
