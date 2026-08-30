[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$StateRoot = (Join-Path $env:LOCALAPPDATA 'XScan'),
    [switch]$StartLegacy,
    [string]$LegacyStartScript = $env:XSCAN_LEGACY_START_SCRIPT
)

$ErrorActionPreference = 'Stop'
$TaskName = 'XScan V2'
$SettingsPath = Join-Path $StateRoot 'settings.json'

function Set-JsonProperty {
    param(
        [Parameter(Mandatory = $true)][object]$InputObject,
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)]$Value
    )
    $Property = $InputObject.PSObject.Properties[$Name]
    if ($null -eq $Property) {
        Add-Member -InputObject $InputObject -MemberType NoteProperty -Name $Name -Value $Value
    } else {
        $Property.Value = $Value
    }
}

function Write-JsonUtf8NoBom {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][object]$Value
    )
    $Json = $Value | ConvertTo-Json -Depth 12
    $Encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Json + [Environment]::NewLine, $Encoding)
}

if ($PSCmdlet.ShouldProcess('XScan V2', 'Disable hardware control and stop V2')) {
    Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue | Stop-ScheduledTask -ErrorAction SilentlyContinue
    Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue | Disable-ScheduledTask | Out-Null
    Get-Process -Name XScanV2, DSDPlus, FMP24, ffmpeg, mediamtx -ErrorAction SilentlyContinue | Stop-Process -Force
    $IsAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if ($IsAdmin) {
        Get-NetFirewallRule -DisplayName 'XScan V2 LAN HTTP','XScan V2 Block Direct Tailscale HTTP' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    }
    if (Test-Path -LiteralPath $SettingsPath) {
        $Settings = Get-Content -LiteralPath $SettingsPath -Raw | ConvertFrom-Json
        Set-JsonProperty -InputObject $Settings.server -Name 'port' -Value 8891
        Set-JsonProperty -InputObject $Settings.runtime -Name 'hardware_control_enabled' -Value $false
        Set-JsonProperty -InputObject $Settings.runtime -Name 'desired_running' -Value $false
        Write-JsonUtf8NoBom -Path $SettingsPath -Value $Settings
    }
    if ($StartLegacy) {
        if (-not $LegacyStartScript -or -not (Test-Path -LiteralPath $LegacyStartScript -PathType Leaf)) {
            throw 'Use -LegacyStartScript C:\path\start_xscan_system.bat (or XSCAN_LEGACY_START_SCRIPT) with -StartLegacy.'
        }
        Start-Process -FilePath $LegacyStartScript -WorkingDirectory (Split-Path -Parent $LegacyStartScript)
    }
}

Write-Host 'XScan V2 is stopped and hardware control is locked. The legacy installation remains intact.'
