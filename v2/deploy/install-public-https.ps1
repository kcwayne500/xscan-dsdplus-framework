[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory = $true)][string]$Domain,
    [int]$BackendPort = 8890,
    [int]$WebRtcMediaPort = 8189,
    [string]$CaddyExe = '',
    [string]$InstallRoot = 'C:\ProgramData\XScan\Caddy'
)

$ErrorActionPreference = 'Stop'
$ServiceName = 'XScanCaddy'
$SourceCaddyfile = Join-Path $PSScriptRoot 'Caddyfile'
$InstalledCaddy = Join-Path $InstallRoot 'caddy.exe'
$InstalledCaddyfile = Join-Path $InstallRoot 'Caddyfile'
$LogRoot = Join-Path $InstallRoot 'logs'

$IsAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)
if (-not $IsAdmin) { throw 'This installer must run from an elevated PowerShell session.' }
if ($Domain -notmatch '^(?=.{1,253}$)([a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}$') {
    throw "Invalid public DNS name: $Domain"
}
if (-not (Test-Path -LiteralPath $SourceCaddyfile -PathType Leaf)) { throw "Caddyfile not found: $SourceCaddyfile" }

$DownloadedCaddyRoot = ''
if (-not $CaddyExe) {
    $DownloadedCaddyRoot = Join-Path ([IO.Path]::GetTempPath()) ("xscan-caddy-" + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $DownloadedCaddyRoot | Out-Null
    $Release = Invoke-RestMethod -Uri 'https://api.github.com/repos/caddyserver/caddy/releases/latest' -Headers @{ 'User-Agent' = 'XScan-setup' }
    $Asset = $Release.assets | Where-Object { $_.name -match '_windows_amd64\.zip$' } | Select-Object -First 1
    if (-not $Asset) { throw 'The latest Caddy release has no Windows amd64 archive.' }
    $Archive = Join-Path $DownloadedCaddyRoot 'caddy.zip'
    Invoke-WebRequest -Uri $Asset.browser_download_url -OutFile $Archive -UseBasicParsing
    Expand-Archive -LiteralPath $Archive -DestinationPath $DownloadedCaddyRoot -Force
    $CaddyExe = Get-ChildItem $DownloadedCaddyRoot -Filter caddy.exe -File -Recurse | Select-Object -First 1 -ExpandProperty FullName
}
if (-not $CaddyExe -or -not (Test-Path -LiteralPath $CaddyExe -PathType Leaf)) { throw "Caddy executable not found: $CaddyExe" }

if ($PSCmdlet.ShouldProcess($InstallRoot, "Install public HTTPS for $Domain")) {
    New-Item -ItemType Directory -Force -Path $InstallRoot, $LogRoot | Out-Null
    Copy-Item -LiteralPath $CaddyExe -Destination $InstalledCaddy -Force

    $Config = Get-Content -LiteralPath $SourceCaddyfile -Raw
    $CaddyLogRoot = $LogRoot.Replace('\', '/')
    $Config = $Config.Replace('__XSCAN_DOMAIN__', $Domain).Replace('__XSCAN_BACKEND__', "127.0.0.1:$BackendPort").Replace('__XSCAN_LOG_ROOT__', $CaddyLogRoot)
    [IO.File]::WriteAllText($InstalledCaddyfile, $Config, [Text.UTF8Encoding]::new($false))

    & $InstalledCaddy validate --config $InstalledCaddyfile --adapter caddyfile
    if ($LASTEXITCODE -ne 0) { throw 'Caddy configuration validation failed.' }

    $ExistingService = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($ExistingService) {
        if ($ExistingService.Status -ne 'Stopped') {
            Stop-Service -Name $ServiceName -Force
            $ExistingService.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(20))
        }
        & sc.exe delete $ServiceName | Out-Null
        Start-Sleep -Seconds 1
    }

    $BinaryPath = "`"$InstalledCaddy`" run --config `"$InstalledCaddyfile`" --adapter caddyfile"
    New-Service -Name $ServiceName -BinaryPathName $BinaryPath -DisplayName 'XScan Public HTTPS (Caddy)' -Description 'HTTPS reverse proxy and automatic Let''s Encrypt certificate renewal for XScan.' -StartupType Automatic | Out-Null
    & sc.exe failure $ServiceName reset= 86400 actions= restart/5000/restart/15000/restart/60000 | Out-Null
    & sc.exe failureflag $ServiceName 1 | Out-Null

    Get-NetFirewallRule -DisplayName 'XScan Public HTTP','XScan Public HTTPS','XScan WebRTC UDP','XScan WebRTC TCP' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    Get-NetFirewallRule -ErrorAction SilentlyContinue | Where-Object {
        $_.DisplayName -in @('xscanv2.exe','XScan V2 LAN HTTP','XScan V2 Block Direct Tailscale HTTP')
    } | Remove-NetFirewallRule
    New-NetFirewallRule -DisplayName 'XScan Public HTTP' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 80 -Profile Any | Out-Null
    New-NetFirewallRule -DisplayName 'XScan Public HTTPS' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 443 -Profile Any | Out-Null
    New-NetFirewallRule -DisplayName 'XScan WebRTC UDP' -Direction Inbound -Action Allow -Protocol UDP -LocalPort $WebRtcMediaPort -Profile Any | Out-Null
    New-NetFirewallRule -DisplayName 'XScan WebRTC TCP' -Direction Inbound -Action Allow -Protocol TCP -LocalPort $WebRtcMediaPort -Profile Any | Out-Null

    Start-Service -Name $ServiceName
    (Get-Service -Name $ServiceName).WaitForStatus('Running', [TimeSpan]::FromSeconds(20))
}

if ($DownloadedCaddyRoot) {
    $ResolvedDownload = [IO.Path]::GetFullPath($DownloadedCaddyRoot)
    if ($ResolvedDownload.StartsWith([IO.Path]::GetFullPath([IO.Path]::GetTempPath()), [StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $ResolvedDownload -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "Caddy is serving https://$Domain and proxying to http://127.0.0.1:$BackendPort"
Write-Host "Ports 80/443 TCP and $WebRtcMediaPort UDP/TCP are allowed through Windows Firewall; certificate renewal is automatic."
