[CmdletBinding()]
param(
    [string]$InstallDir = 'C:\DSDPlusFastlane',
    [string]$PublicHost = 'xscan.cc-group.org',
    [int]$RtlIndex = 2,
    [string]$DsdAudioOutput = '2M',
    [switch]$SkipDriverInstall,
    [switch]$SkipSystemChanges,
    [switch]$NoLaunch
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$sourceRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$scriptPath = $MyInvocation.MyCommand.Path
$runtimeRoot = Join-Path $sourceRoot 'runtime'
$InstallDir = [IO.Path]::GetFullPath($InstallDir)
$appDir = Join-Path $InstallDir 'scanner-recorder-repo'
$startupDir = Join-Path $InstallDir 'startup'
$recordingsDir = Join-Path $InstallDir 'recordings'
$logPath = Join-Path $InstallDir 'install.log'
$rebootRecommended = $false
$warnings = [System.Collections.Generic.List[string]]::new()

function Write-InstallLog {
    param([string]$Message, [ConsoleColor]$Color = [ConsoleColor]::Gray)
    $timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Write-Host $Message -ForegroundColor $Color
    try {
        if (Test-Path -LiteralPath $InstallDir) {
            Add-Content -LiteralPath $logPath -Value "[$timestamp] $Message" -Encoding UTF8
        }
    } catch {}
}

function Add-Warning {
    param([string]$Message)
    $warnings.Add($Message)
    Write-InstallLog "WARNING: $Message" Yellow
}

function Write-Utf8NoBom {
    param([string]$Path, [string]$Content)
    [IO.File]::WriteAllText($Path, $Content, [Text.UTF8Encoding]::new($false))
}

function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Invoke-ElevatedInstaller {
    $arguments = @('-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"{0}"' -f $scriptPath))
    $arguments += @('-InstallDir', ('"{0}"' -f $InstallDir), '-PublicHost', ('"{0}"' -f $PublicHost), '-RtlIndex', $RtlIndex, '-DsdAudioOutput', ('"{0}"' -f $DsdAudioOutput))
    if ($SkipDriverInstall) { $arguments += '-SkipDriverInstall' }
    if ($NoLaunch) { $arguments += '-NoLaunch' }
    $process = Start-Process -FilePath 'powershell.exe' -ArgumentList $arguments -Verb RunAs -Wait -PassThru
    exit $process.ExitCode
}

function Test-LfsPointer {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }
    if ((Get-Item -LiteralPath $Path).Length -gt 1024) { return $false }
    $firstLine = Get-Content -LiteralPath $Path -TotalCount 1 -ErrorAction SilentlyContinue
    return $firstLine -eq 'version https://git-lfs.github.com/spec/v1'
}

function Restore-LfsPayload {
    $probe = Join-Path $runtimeRoot 'caddy\caddy.exe'
    if (-not (Test-LfsPointer $probe)) { return }
    Write-InstallLog 'Restoring Git LFS runtime payload...'
    $git = Get-Command git.exe -ErrorAction SilentlyContinue
    if ($null -eq $git) { throw 'Git LFS payload is missing and Git is not installed.' }
    & $git.Source -C $sourceRoot lfs install
    if ($LASTEXITCODE -ne 0) { throw 'git lfs install failed.' }
    & $git.Source -C $sourceRoot lfs pull
    if ($LASTEXITCODE -ne 0) { throw 'git lfs pull failed. Confirm GitHub authentication and LFS quota.' }
}

function Assert-Payload {
    $required = @(
        (Join-Path $runtimeRoot 'dsdplus\DSDPlus.exe'),
        (Join-Path $runtimeRoot 'dsdplus\FMP24.exe'),
        (Join-Path $runtimeRoot 'ffmpeg\ffmpeg.exe'),
        (Join-Path $runtimeRoot 'mediamtx\mediamtx.exe'),
        (Join-Path $runtimeRoot 'caddy\caddy.exe'),
        (Join-Path $runtimeRoot 'startup\start_scan_stack.py'),
        (Join-Path $runtimeRoot 'START_EVERYTHING.cmd')
    )
    foreach ($path in $required) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Required payload file is missing: $path" }
        if (Test-LfsPointer $path) { throw "Git LFS did not restore: $path" }
    }
}

function Get-PythonExecutable {
    $candidates = [System.Collections.Generic.List[string]]::new()
    $command = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($null -ne $command) { $candidates.Add($command.Source) }
    foreach ($pattern in @(
        "$env:ProgramFiles\Python*\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python*\python.exe",
        "$env:LOCALAPPDATA\Microsoft\WindowsApps\PythonSoftwareFoundation.Python*\python.exe"
    )) {
        Get-ChildItem -Path $pattern -File -ErrorAction SilentlyContinue | ForEach-Object { $candidates.Add($_.FullName) }
    }
    foreach ($candidate in $candidates | Select-Object -Unique) {
        try {
            $versionText = (& $candidate --version 2>&1 | Select-Object -Last 1) -replace '^Python\s+', ''
            if ($LASTEXITCODE -eq 0 -and [version]$versionText -ge [version]'3.11') { return $candidate }
        } catch {}
    }
    return $null
}

function Install-PythonIfNeeded {
    $python = Get-PythonExecutable
    if ($python) { return $python }
    if ($SkipSystemChanges) { throw 'Python 3.11+ is required for a staging install.' }
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if ($null -eq $winget) { throw 'Python 3.11+ is missing and winget is unavailable. Install Python, then rerun INSTALL.cmd.' }
    Write-InstallLog 'Installing Python 3.13 with winget...'
    & $winget.Source install --id Python.Python.3.13 --exact --scope machine --silent --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { throw "Python installation failed with exit code $LASTEXITCODE." }
    $python = Get-PythonExecutable
    if (-not $python) { throw 'Python was installed but could not be located. Restart Windows and rerun INSTALL.cmd.' }
    return $python
}

function Copy-Application {
    Write-InstallLog "Deploying XScan to $InstallDir..."
    New-Item -ItemType Directory -Path $InstallDir, $appDir, $startupDir, $recordingsDir -Force | Out-Null

    $sourceApp = [IO.Path]::GetFullPath($sourceRoot).TrimEnd('\')
    $targetApp = [IO.Path]::GetFullPath($appDir).TrimEnd('\')
    if ($sourceApp -ne $targetApp) {
        foreach ($file in @('app.ico', 'requirements.txt', 'scanner_core.py', 'scanner_gui_recorder.py', 'scanner_web_server.py', 'streaming_support.py')) {
            Copy-Item -LiteralPath (Join-Path $sourceRoot $file) -Destination $appDir -Force
        }
        Copy-Item -LiteralPath (Join-Path $sourceRoot 'webui') -Destination $appDir -Recurse -Force
    }

    Get-ChildItem -LiteralPath (Join-Path $runtimeRoot 'dsdplus') -File | Copy-Item -Destination $InstallDir -Force
    Copy-Item -LiteralPath (Join-Path $runtimeRoot 'mediamtx') -Destination $InstallDir -Recurse -Force
    Copy-Item -LiteralPath (Join-Path $runtimeRoot 'ffmpeg') -Destination $appDir -Recurse -Force
    Copy-Item -LiteralPath (Join-Path $runtimeRoot 'startup\start_scan_stack.py') -Destination $startupDir -Force
    Copy-Item -LiteralPath (Join-Path $runtimeRoot 'START_EVERYTHING.cmd') -Destination $InstallDir -Force

    $settingsPath = Join-Path $appDir 'scanner_gui_recorder_settings.json'
    if (-not (Test-Path -LiteralPath $settingsPath)) {
        Copy-Item -LiteralPath (Join-Path $sourceRoot 'config\scanner_gui_recorder_settings.json') -Destination $settingsPath
    }

    $stackConfig = [ordered]@{
        link_id = 20001
        rtl_index = $RtlIndex
        dsd_audio_output = $DsdAudioOutput
        dsd_filename_modifier = 6
    }
    Write-Utf8NoBom -Path (Join-Path $startupDir 'stack_config.json') -Content (($stackConfig | ConvertTo-Json) + "`n")
    Write-Utf8NoBom -Path (Join-Path $InstallDir 'install_config.json') -Content (([ordered]@{ public_host = $PublicHost } | ConvertTo-Json) + "`n")

    $recordingsLog = Join-Path $recordingsDir 'recordings_log.json'
    if (-not (Test-Path -LiteralPath $recordingsLog)) { Write-Utf8NoBom -Path $recordingsLog -Content "[]`n" }
}

function Install-PythonEnvironment {
    param([string]$Python)
    $venvPython = Join-Path $InstallDir '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $venvPython)) {
        Write-InstallLog 'Creating the isolated Python environment...'
        & $Python -m venv (Join-Path $InstallDir '.venv')
        if ($LASTEXITCODE -ne 0) { throw 'Python virtual environment creation failed.' }
    }
    Write-InstallLog 'Installing scanner Python dependencies...'
    & $venvPython -m pip install --disable-pip-version-check --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw 'pip upgrade failed.' }
    & $venvPython -m pip install --disable-pip-version-check -r (Join-Path $appDir 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
    & $venvPython (Join-Path $sourceRoot 'scripts\configure_audio.py') (Join-Path $appDir 'scanner_gui_recorder_settings.json')
    if ($LASTEXITCODE -ne 0) { Add-Warning 'VB-CABLE input was not detected; audio selection must be completed after installing/rebooting the driver.' }
    return $venvPython
}

function Test-VbCable {
    return $null -ne (Get-CimInstance Win32_SoundDevice -ErrorAction SilentlyContinue | Where-Object { $_.Name -match 'VB-Audio Virtual Cable' } | Select-Object -First 1)
}

function Install-VbCableIfNeeded {
    if (Test-VbCable) {
        Write-InstallLog 'VB-CABLE audio driver is already installed.' Green
        return
    }
    if ($SkipDriverInstall -or $SkipSystemChanges) {
        Add-Warning 'VB-CABLE is not installed. Run INSTALL.cmd without -SkipDriverInstall and reboot.'
        return
    }
    $driverDir = Join-Path $InstallDir 'drivers\VBCABLE'
    $zipPath = Join-Path $InstallDir 'drivers\VBCABLE_Driver_Pack45.zip'
    New-Item -ItemType Directory -Path (Split-Path $zipPath), $driverDir -Force | Out-Null
    Write-InstallLog 'Downloading the official VB-CABLE driver...'
    Invoke-WebRequest -Uri 'https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack45.zip' -OutFile $zipPath -UseBasicParsing
    Expand-Archive -LiteralPath $zipPath -DestinationPath $driverDir -Force
    $setup = Get-ChildItem -LiteralPath $driverDir -Recurse -Filter 'VBCABLE_Setup_x64.exe' | Select-Object -First 1
    if ($null -eq $setup) { throw 'The VB-CABLE x64 setup program was not found in the official package.' }
    Write-InstallLog 'Complete the VB-CABLE setup window. A reboot is required afterward.' Cyan
    $process = Start-Process -FilePath $setup.FullName -Wait -PassThru
    if ($process.ExitCode -ne 0) { Add-Warning "VB-CABLE setup returned exit code $($process.ExitCode)." }
    $script:rebootRecommended = $true
}

function Test-RtlSdrDriver {
    $device = Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | Where-Object {
        $_.InstanceId -match 'VID_0BDA&PID_2838' -and $_.Class -eq 'USBDevice'
    } | Select-Object -First 1
    return $null -ne $device
}

function Install-Caddy {
    if ($SkipSystemChanges) { return }
    $caddyDir = 'C:\Caddy'
    $caddyExe = Join-Path $caddyDir 'caddy.exe'
    $caddyfile = Join-Path $caddyDir 'Caddyfile'
    New-Item -ItemType Directory -Path $caddyDir -Force | Out-Null
    $service = Get-Service -Name 'Caddy' -ErrorAction SilentlyContinue
    if ($null -ne $service -and $service.Status -ne 'Stopped') {
        Stop-Service -Name 'Caddy' -Force
        $service.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(15))
    }
    Copy-Item -LiteralPath (Join-Path $runtimeRoot 'caddy\caddy.exe') -Destination $caddyExe -Force
    $caddyContent = @"
$PublicHost {
    reverse_proxy 127.0.0.1:8890
}
"@
    Write-Utf8NoBom -Path $caddyfile -Content $caddyContent
    & $caddyExe validate --config $caddyfile
    if ($LASTEXITCODE -ne 0) { throw 'Caddy configuration validation failed.' }
    $binaryPath = '"C:\Caddy\caddy.exe" run --config "C:\Caddy\Caddyfile"'
    if ($null -eq $service) {
        New-Service -Name 'Caddy' -BinaryPathName $binaryPath -DisplayName 'Caddy' -StartupType Automatic | Out-Null
    } else {
        & sc.exe config Caddy "binPath= $binaryPath" 'start= auto' | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Caddy service configuration failed.' }
    }
    Start-Service -Name 'Caddy'
    (Get-Service -Name 'Caddy').WaitForStatus('Running', [TimeSpan]::FromSeconds(15))
    Write-InstallLog 'Caddy HTTPS proxy is installed and running.' Green
}

function Ensure-FirewallRule {
    param([string]$Name, [string]$Program, [string]$Protocol, [int]$Port)
    if ($SkipSystemChanges) { return }
    $rule = Get-NetFirewallRule -DisplayName $Name -ErrorAction SilentlyContinue
    if ($null -ne $rule) { Remove-NetFirewallRule -DisplayName $Name }
    New-NetFirewallRule -DisplayName $Name -Direction Inbound -Action Allow -Program $Program -Protocol $Protocol -LocalPort $Port -Profile Any | Out-Null
}

function Configure-Network {
    if ($SkipSystemChanges) { return }
    Ensure-FirewallRule -Name 'Caddy HTTP 80' -Program 'C:\Caddy\caddy.exe' -Protocol TCP -Port 80
    Ensure-FirewallRule -Name 'Caddy HTTPS 443' -Program 'C:\Caddy\caddy.exe' -Protocol TCP -Port 443
    Ensure-FirewallRule -Name 'MediaMTX WebRTC UDP 8189' -Program (Join-Path $InstallDir 'mediamtx\mediamtx.exe') -Protocol UDP -Port 8189
    Write-InstallLog 'Windows Firewall rules are installed.' Green

    try {
        $lanIp = (Get-NetRoute -DestinationPrefix '0.0.0.0/0' | Sort-Object RouteMetric | ForEach-Object {
            Get-NetIPAddress -InterfaceIndex $_.InterfaceIndex -AddressFamily IPv4 -AddressState Preferred -ErrorAction SilentlyContinue
        } | Where-Object { $_.IPAddress -notlike '169.254.*' } | Select-Object -First 1 -ExpandProperty IPAddress)
        $collection = (New-Object -ComObject HNetCfg.NATUPnP).StaticPortMappingCollection
        if ($null -eq $collection -or -not $lanIp) { throw 'UPnP is unavailable.' }
        foreach ($mapping in @(
            @{ Port = 80; Protocol = 'TCP'; Description = 'xscan Caddy HTTP' },
            @{ Port = 443; Protocol = 'TCP'; Description = 'xscan Caddy HTTPS' },
            @{ Port = 8189; Protocol = 'UDP'; Description = 'xscan MediaMTX WebRTC' }
        )) {
            $existing = @($collection | Where-Object { $_.ExternalPort -eq $mapping.Port -and $_.Protocol -eq $mapping.Protocol }) | Select-Object -First 1
            if ($existing -and $existing.InternalClient -eq $lanIp -and $existing.InternalPort -eq $mapping.Port) { continue }
            if ($existing) {
                Add-Warning "Router $($mapping.Protocol) $($mapping.Port) already points to $($existing.InternalClient):$($existing.InternalPort). Correct it manually."
                continue
            }
            try {
                [void]$collection.Add($mapping.Port, $mapping.Protocol, $mapping.Port, $lanIp, $true, $mapping.Description)
            } catch {
                Add-Warning "Router could not map $($mapping.Protocol) $($mapping.Port): $($_.Exception.Message)"
            }
        }
    } catch {
        Add-Warning "Automatic router configuration was unavailable: $($_.Exception.Message) Forward TCP 80/443 and UDP 8189 to this PC manually."
    }
}

function Create-DesktopShortcut {
    if ($SkipSystemChanges) {
        Write-InstallLog "Skipping desktop shortcut because -SkipSystemChanges was selected."
        return
    }

    $desktop = [Environment]::GetFolderPath('Desktop')
    if (-not $desktop) { return }
    $shortcutPath = Join-Path $desktop 'Start XScan.lnk'
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = Join-Path $InstallDir 'START_EVERYTHING.cmd'
    $shortcut.WorkingDirectory = $InstallDir
    $shortcut.IconLocation = Join-Path $appDir 'app.ico'
    $shortcut.Description = 'Start and verify the complete XScan DSDPlus system'
    $shortcut.Save()
    Write-InstallLog "Desktop shortcut created: $shortcutPath" Green
}

try {
    if (-not $SkipSystemChanges -and -not (Test-Administrator)) {
        Write-Host 'Administrator access is required for drivers, Caddy, and firewall rules.' -ForegroundColor Cyan
        Invoke-ElevatedInstaller
    }

    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
    Write-InstallLog 'XScan complete installation starting...' Cyan
    Restore-LfsPayload
    Assert-Payload
    $python = Install-PythonIfNeeded
    Copy-Application
    Install-VbCableIfNeeded
    $venvPython = Install-PythonEnvironment -Python $python

    if (-not (Test-RtlSdrDriver)) {
        Add-Warning 'RTL-SDR WinUSB device was not detected. Connect the dongle and assign WinUSB to both Bulk-In interfaces with Zadig: https://zadig.akeo.ie/'
    } else {
        Write-InstallLog 'RTL-SDR WinUSB device detected.' Green
    }

    Install-Caddy
    Configure-Network
    Create-DesktopShortcut

    & (Join-Path $sourceRoot 'VERIFY_INSTALL.ps1') -InstallDir $InstallDir -Offline
    if ($LASTEXITCODE -ne 0) { throw 'Offline installation verification failed.' }

    Write-InstallLog ''
    Write-InstallLog 'Installation files and dependencies are complete.' Green
    if ($warnings.Count) {
        Write-InstallLog 'Review these machine-specific warnings:' Yellow
        foreach ($warning in $warnings) { Write-InstallLog "  - $warning" Yellow }
    }
    if ($rebootRecommended) {
        Write-InstallLog 'REBOOT WINDOWS, then run START_EVERYTHING.cmd.' Yellow
    } elseif (-not $NoLaunch -and $warnings.Count -eq 0) {
        Write-InstallLog 'Launching XScan...'
        Start-Process -FilePath (Join-Path $InstallDir 'START_EVERYTHING.cmd') -WorkingDirectory $InstallDir
    } else {
        Write-InstallLog "Launch when ready: $(Join-Path $InstallDir 'START_EVERYTHING.cmd')" Cyan
    }
    exit 0
} catch {
    Write-InstallLog "INSTALLATION FAILED: $($_.Exception.Message)" Red
    Write-InstallLog "Log: $logPath" Yellow
    exit 1
}
