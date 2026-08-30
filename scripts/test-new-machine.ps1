[CmdletBinding()]
param(
    [string]$DsdPlusRoot = 'C:\DSDPlusFastLane',
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'Programs\XScan'),
    [string]$StateRoot = (Join-Path $env:LOCALAPPDATA 'XScan'),
    [switch]$Cutover
)

$ErrorActionPreference = 'Stop'
$Failures = [Collections.Generic.List[string]]::new()
$Warnings = [Collections.Generic.List[string]]::new()
$Port = if ($Cutover) { 8890 } else { 8891 }

function Require-File {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { $Failures.Add("Missing file: $Path") }
}

Require-File (Join-Path $DsdPlusRoot 'DSDPlus.exe')
Require-File (Join-Path $DsdPlusRoot 'FMP24.exe')
Require-File (Join-Path $InstallRoot 'XScanV2.exe')
Require-File (Join-Path $StateRoot 'settings.json')

$CableDevices = @(Get-CimInstance Win32_SoundDevice -ErrorAction SilentlyContinue | Where-Object {
    $_.Name -match 'VB-Audio|CABLE Input|CABLE Output'
})
if ($CableDevices.Count -eq 0) { $Failures.Add('VB-CABLE was not detected by Windows.') }

$UsbDevices = @(Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | Where-Object {
    $_.FriendlyName -match 'RTL|Bulk-In|RTL283|WinUSB' -or $_.InstanceId -match 'VID_0BDA&PID_2838'
})
if ($UsbDevices.Count -eq 0) { $Warnings.Add('No recognizable RTL-SDR USB device was found. Confirm it in Device Manager and Zadig.') }

if ($Cutover) {
    $Task = Get-ScheduledTask -TaskName 'XScan V2' -ErrorAction SilentlyContinue
    if (-not $Task) { $Failures.Add('The XScan V2 scheduled task is missing.') }
    foreach ($ProcessName in @('XScanV2', 'FMP24', 'DSDPlus')) {
        if (-not (Get-Process -Name $ProcessName -ErrorAction SilentlyContinue)) {
            $Failures.Add("Expected process is not running: $ProcessName")
        }
    }
}

try {
    $Response = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/" -UseBasicParsing -TimeoutSec 10
    if ($Response.StatusCode -ne 200) { $Failures.Add("Dashboard returned HTTP $($Response.StatusCode) on port $Port.") }
} catch {
    $Failures.Add("Dashboard did not answer on http://127.0.0.1:$Port/: $($_.Exception.Message)")
}

$LogRoot = Join-Path $StateRoot 'logs'
if (Test-Path -LiteralPath $LogRoot) {
    $RecentLogs = @(Get-ChildItem -LiteralPath $LogRoot -File -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 5)
    foreach ($Log in $RecentLogs) {
        $Hits = Select-String -LiteralPath $Log.FullName -Pattern 'I/Q loss|IQ loss|overrun|USB.*loss|restart loop' -SimpleMatch:$false -ErrorAction SilentlyContinue
        if ($Hits) { $Warnings.Add("Possible signal/transport problem in $($Log.FullName); inspect recent I/Q loss or overrun entries.") }
    }
}

foreach ($Warning in ($Warnings | Sort-Object -Unique)) { Write-Warning $Warning }
if ($Failures.Count -gt 0) {
    foreach ($Failure in ($Failures | Sort-Object -Unique)) { Write-Host "FAIL: $Failure" -ForegroundColor Red }
    throw "New-machine validation failed with $($Failures.Count) finding(s)."
}

Write-Host "PASS: XScan dashboard answered on port $Port."
Write-Host "PASS: DSDPlus/FMP24, XScan, settings, and VB-CABLE checks succeeded."
if (-not $Cutover) { Write-Host 'Side-by-side validation passed. Install hardware drivers, reboot, and rerun install-new-machine.ps1 -Cutover.' }
