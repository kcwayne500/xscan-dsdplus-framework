[CmdletBinding(SupportsShouldProcess)]
param(
    [switch]$Cutover,
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'Programs\XScan'),
    [string]$StateRoot = (Join-Path $env:LOCALAPPDATA 'XScan'),
    [string]$DsdPlusRoot = (Join-Path $env:LOCALAPPDATA 'Programs\DSDPlus'),
    [string]$FfmpegExe = '',
    [string]$MediaMtxExe = '',
    [string]$PublicUrl = ''
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$BuildRoot = Join-Path $ProjectRoot 'dist\XScanV2'
$Executable = Join-Path $InstallRoot 'XScanV2.exe'
$SettingsPath = Join-Path $StateRoot 'settings.json'
$TaskName = 'XScan V2'
$Port = if ($Cutover) { 8890 } else { 8891 }
$EffectivePublicUrl = ''
$Installed = $false

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

if (-not (Test-Path -LiteralPath (Join-Path $BuildRoot 'XScanV2.exe'))) {
    throw "Build output is missing. Run .\build.ps1 first."
}
if ($Cutover) {
    foreach ($Name in @('DSDPlus.exe', 'FMP24.exe')) {
        $Required = Join-Path $DsdPlusRoot $Name
        if (-not (Test-Path -LiteralPath $Required -PathType Leaf)) { throw "Required DSDPlus component is missing: $Required" }
    }
}
foreach ($Tool in @($FfmpegExe, $MediaMtxExe)) {
    if ($Tool -and -not (Test-Path -LiteralPath $Tool -PathType Leaf)) { throw "Configured tool was not found: $Tool" }
}

if ($Cutover) {
    Write-Warning 'CUTOVER stops the legacy recorder/receiver processes and enables V2 hardware control on port 8890.'
}

if ($PSCmdlet.ShouldProcess($InstallRoot, 'Install XScan V2')) {
    Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue | Stop-ScheduledTask -ErrorAction SilentlyContinue
    Get-Process -Name XScanV2 -ErrorAction SilentlyContinue | Stop-Process -Force
    if ($Cutover) {
        # Forced task termination does not guarantee that supervised children
        # have released their executable files before the deployment copy.
        Get-Process -Name DSDPlusScannerRecorder, DSDPlus, FMP24, ffmpeg, mediamtx -ErrorAction SilentlyContinue | Stop-Process -Force
        Start-Sleep -Milliseconds 500
    }
    New-Item -ItemType Directory -Force -Path $InstallRoot, $StateRoot | Out-Null
    Copy-Item -Path (Join-Path $BuildRoot '*') -Destination $InstallRoot -Recurse -Force

    $Settings = [pscustomobject]@{}
    if (Test-Path -LiteralPath $SettingsPath) {
        $Settings = Get-Content -LiteralPath $SettingsPath -Raw | ConvertFrom-Json
        Copy-Item -LiteralPath $SettingsPath -Destination "$SettingsPath.pre-install.bak" -Force
    }
    if ($null -eq $Settings) { $Settings = [pscustomobject]@{} }
    if ($null -eq $Settings.PSObject.Properties['server'] -or $null -eq $Settings.server) {
        Set-JsonProperty -InputObject $Settings -Name 'server' -Value ([pscustomobject]@{})
    }
    if ($null -eq $Settings.PSObject.Properties['runtime'] -or $null -eq $Settings.runtime) {
        Set-JsonProperty -InputObject $Settings -Name 'runtime' -Value ([pscustomobject]@{})
    }
    if ($null -eq $Settings.PSObject.Properties['tools'] -or $null -eq $Settings.tools) {
        Set-JsonProperty -InputObject $Settings -Name 'tools' -Value ([pscustomobject]@{})
    }
    Set-JsonProperty -InputObject $Settings.server -Name 'port' -Value $Port
    Set-JsonProperty -InputObject $Settings.server -Name 'host' -Value '127.0.0.1'
    Set-JsonProperty -InputObject $Settings.server -Name 'lan_http_enabled' -Value $false
    $EffectivePublicUrl = if ($PublicUrl) { $PublicUrl.TrimEnd('/') } elseif ($Settings.server.public_url) { [string]$Settings.server.public_url } else { '' }
    if ($EffectivePublicUrl -and $EffectivePublicUrl -notmatch '^https://') { throw 'PublicUrl must be empty or begin with https://' }
    Set-JsonProperty -InputObject $Settings.server -Name 'public_https_enabled' -Value ([bool]$EffectivePublicUrl)
    Set-JsonProperty -InputObject $Settings.server -Name 'public_url' -Value $EffectivePublicUrl
    $Settings.server.PSObject.Properties.Remove('tailscale_https_enabled')
    $Settings.server.PSObject.Properties.Remove('tailscale_url')
    Set-JsonProperty -InputObject $Settings.runtime -Name 'hardware_control_enabled' -Value ([bool]$Cutover)
    Set-JsonProperty -InputObject $Settings.runtime -Name 'desired_running' -Value ([bool]$Cutover)
    if ($FfmpegExe) { Set-JsonProperty -InputObject $Settings.tools -Name 'ffmpeg' -Value ([IO.Path]::GetFullPath($FfmpegExe)) }
    if ($MediaMtxExe) { Set-JsonProperty -InputObject $Settings.tools -Name 'mediamtx' -Value ([IO.Path]::GetFullPath($MediaMtxExe)) }
    # DSDPlus -m2 is the persistent mixed analog/digital behavior XScan needs:
    # pass source audio whenever no digital sync is present.
    $RawDsdArgs = @($Settings.runtime.dsdplus_args)
    if ($RawDsdArgs.Count -eq 0) { $RawDsdArgs = @('-r1', '-i20001') }
    $DsdArgs = @($RawDsdArgs | Where-Object { $_ -notmatch '^-m[0-4]$' })
    if ($DsdArgs.Count -gt 0 -and $DsdArgs[0] -match '^-r') {
        $DsdArgs = @($DsdArgs[0], '-m2') + @($DsdArgs | Select-Object -Skip 1)
    } else {
        $DsdArgs = @('-m2') + $DsdArgs
    }
    Set-JsonProperty -InputObject $Settings.runtime -Name 'dsdplus_args' -Value $DsdArgs
    Write-JsonUtf8NoBom -Path $SettingsPath -Value $Settings

    if ($Cutover) {
        $TaskArguments = "--state-dir `"$StateRoot`" --dsdplus-root `"$DsdPlusRoot`""
        $Action = New-ScheduledTaskAction -Execute $Executable -Argument $TaskArguments
        $Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
        $SettingsSet = New-ScheduledTaskSettingsSet -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable -MultipleInstances IgnoreNew
        Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $SettingsSet -Description 'XScan V2 DSDPlus scanner host' -Force | Out-Null
        Start-ScheduledTask -TaskName $TaskName
    } else {
        $LaunchArguments = '--state-dir "{0}" --dsdplus-root "{1}"' -f $StateRoot, $DsdPlusRoot
        Start-Process -FilePath $Executable -ArgumentList $LaunchArguments -WindowStyle Hidden
    }

    $IsAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if ($IsAdmin) {
        Get-NetFirewallRule -DisplayName 'XScan V2 LAN HTTP','XScan V2 Block Direct Tailscale HTTP' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    }
    $Installed = $true
}

if (-not $Installed) {
    Write-Host 'Install validation completed; no files or processes were changed.'
    return
}
Write-Host "Installed XScan V2 at $InstallRoot"
Write-Host "Local dashboard: http://127.0.0.1:$Port/"
Write-Host "DSDPlus directory: $DsdPlusRoot"
if ($EffectivePublicUrl) {
    Write-Host "Public dashboard: $EffectivePublicUrl"
    Write-Host 'Run deploy\install-public-https.ps1 as administrator to install the Caddy HTTPS service.'
} else {
    Write-Host 'Public HTTPS is disabled. Localhost access remains available.'
}
if (-not $Cutover) { Write-Host 'Side-by-side safety lock remains enabled; the legacy radio pipeline was not touched.' }
