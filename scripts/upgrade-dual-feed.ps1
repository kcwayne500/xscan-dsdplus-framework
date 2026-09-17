# Existing-host upgrade only. Stop both feeds and quit XScan before running.
[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$Candidate = (Join-Path $PSScriptRoot '..\v2\dist\XScanV2'),
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'Programs\XScan'),
    [string]$StateRoot = (Join-Path $env:LOCALAPPDATA 'XScan'),
    [string]$DsdPlusRoot = 'C:\DSDPlusFastLane',
    [string]$Rollback = ''
)
$ErrorActionPreference = 'Stop'
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot).TrimEnd('\')
$StateRoot = [IO.Path]::GetFullPath($StateRoot).TrimEnd('\')
$DsdPlusRoot = [IO.Path]::GetFullPath($DsdPlusRoot).TrimEnd('\')
$Candidate = [IO.Path]::GetFullPath($Candidate).TrimEnd('\')
$Parent = Split-Path -Parent $InstallRoot
if ((Split-Path -Leaf $InstallRoot) -ne 'XScan' -or -not (Test-Path -LiteralPath "$InstallRoot\XScanV2.exe")) {
    throw 'Target must be an existing XScan installation directory named XScan.'
}
if (-not (Test-Path -LiteralPath "$StateRoot\settings.json")) { throw 'Existing settings are required.' }
if ($Candidate -eq $InstallRoot -or $Candidate.StartsWith($InstallRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Build candidate must be outside the installed directory.'
}

function Assert-Quiescent {
    $ScopedRoots = @($InstallRoot, $DsdPlusRoot, "$StateRoot\feeds\feed2\dsdplus")
    $Processes = @(Get-CimInstance Win32_Process)
    $Busy = @($Processes | Where-Object {
        $ExePath = $_.ExecutablePath
        ($ExePath -and @($ScopedRoots | Where-Object { $ExePath.StartsWith($_ + '\', [StringComparison]::OrdinalIgnoreCase) }).Count) -or
        ($_.Name -eq 'mediamtx.exe' -and $_.CommandLine -and $_.CommandLine.Contains("$StateRoot\mediamtx.generated.yml")) -or
        ($_.Name -eq 'XScanV2.exe' -and $_.CommandLine -and $_.CommandLine.Contains($StateRoot))
    })
    if ($Busy.Count) { throw 'Stop both feeds, then quit the installed host before upgrading. No process will be force-killed.' }
    $Settings = Get-Content -LiteralPath "$StateRoot\settings.json" -Raw | ConvertFrom-Json
    $Port = if ($Settings.server.port) { [int]$Settings.server.port } else { 8890 }
    if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) {
        throw "Port $Port is still occupied; verify its owner and quit the host first."
    }
}

# The host lock is held across backups and file moves. Older hosts are additionally
# excluded by the executable/path and port checks. This does not stop autostart jobs.
Assert-Quiescent
$LockPath = Join-Path $StateRoot 'host.lock'
$HostLock = [IO.File]::Open($LockPath, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
try {
    Assert-Quiescent
    $Stamp = (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0,8)
    $CheckpointRoot = Join-Path (Split-Path -Parent $StateRoot) 'XScan-upgrades'
    $Checkpoint = Join-Path $CheckpointRoot $Stamp
    $Previous = Join-Path $Parent "XScan.previous-$Stamp"
    $Staged = Join-Path $Parent "XScan.candidate-$Stamp"
    foreach ($Target in @($Previous, $Staged)) {
        if ((Split-Path -Parent ([IO.Path]::GetFullPath($Target))) -ne $Parent -or (Test-Path -LiteralPath $Target)) {
            throw 'Unsafe or existing version directory.'
        }
    }
    $Source = $Candidate
    if ($Rollback) {
        $Rollback = [IO.Path]::GetFullPath($Rollback).TrimEnd('\')
        if ((Split-Path -Parent $Rollback) -ne $CheckpointRoot) { throw 'Rollback must identify an XScan-upgrades checkpoint.' }
        $Manifest = Get-Content -LiteralPath "$Rollback\manifest.json" -Raw | ConvertFrom-Json
        if ($Manifest.install -ne $InstallRoot -or $Manifest.state -ne $StateRoot) { throw 'Checkpoint target mismatch.' }
        $Source = [string]$Manifest.previous
        if ((Split-Path -Parent $Source) -ne $Parent -or (Split-Path -Leaf $Source) -notlike 'XScan.previous-*') {
            throw 'Invalid rollback executable directory.'
        }
        Write-Warning 'Rollback restores the checkpoint database/settings. Newer state is archived first; newer recording files remain on disk.'
    }
    if (-not (Test-Path -LiteralPath "$Source\XScanV2.exe")) { throw 'Candidate executable is missing.' }
    if (-not $PSCmdlet.ShouldProcess($InstallRoot, 'Checkpoint state and replace installed XScan without changing logon registration')) { return }
    New-Item -ItemType Directory -Path $Checkpoint, "$Checkpoint\state" | Out-Null
    # Preserve root state (including DB WAL), plus Feed 2 settings/config. Exclude
    # recordings/logs/backups and executable dependencies, none of which are changed.
    $StateFiles = @(Get-ChildItem -LiteralPath $StateRoot -File | Where-Object Name -ne 'host.lock')
    if (Test-Path -LiteralPath "$StateRoot\feeds") {
        $StateFiles += @(Get-ChildItem -LiteralPath "$StateRoot\feeds" -File -Recurse | Where-Object {
            $_.FullName -notmatch '\\(recordings|logs|backups)\\' -and $_.Extension -notin '.exe','.dll'
        })
    }
    foreach ($File in $StateFiles) {
        $Relative = $File.FullName.Substring($StateRoot.Length + 1)
        $Destination = Join-Path "$Checkpoint\state" $Relative
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Destination) | Out-Null
        Copy-Item -LiteralPath $File.FullName -Destination $Destination
    }
    Copy-Item -LiteralPath $Source -Destination $Staged -Recurse
    $Hashes = @(Get-ChildItem -LiteralPath "$Checkpoint\state" -Recurse -File | ForEach-Object {
        @{path=$_.FullName.Substring($Checkpoint.Length+1); sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash}
    })
    $Record = @{install=$InstallRoot; state=$StateRoot; previous=$Previous; candidate_sha256=(Get-FileHash -LiteralPath "$Staged\XScanV2.exe").Hash; files=$Hashes}
    [IO.File]::WriteAllText("$Checkpoint\manifest.json", ($Record | ConvertTo-Json -Depth 8))
    if ($Rollback) {
        foreach ($Entry in $Manifest.files) {
            $BackupFile = [IO.Path]::GetFullPath((Join-Path $Rollback $Entry.path))
            if (-not $BackupFile.StartsWith($Rollback + '\state\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid checkpoint file path.' }
            if ((Get-FileHash -LiteralPath $BackupFile).Hash -ne $Entry.sha256) { throw 'Checkpoint hash mismatch.' }
        }
        # Remove only the exact DB trio after archiving it; no recursive deletions.
        foreach ($Name in @('xscan.db','xscan.db-wal','xscan.db-shm')) {
            $DbFile = Join-Path $StateRoot $Name
            if (Test-Path -LiteralPath $DbFile) { Remove-Item -LiteralPath $DbFile }
        }
        foreach ($Entry in $Manifest.files) {
            $Relative = $Entry.path.Substring(6) # strip state\
            $Destination = Join-Path $StateRoot $Relative
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Destination) | Out-Null
            Copy-Item -LiteralPath (Join-Path $Rollback $Entry.path) -Destination $Destination -Force
        }
    }
    Move-Item -LiteralPath $InstallRoot -Destination $Previous
    try { Move-Item -LiteralPath $Staged -Destination $InstallRoot }
    catch {
        Move-Item -LiteralPath $Previous -Destination $InstallRoot
        throw
    }
    Write-Host "Installed candidate. Checkpoint: $Checkpoint"
    Write-Host 'Start with the existing Start-XScan.ps1 launcher, then verify Feed 1 before enabling Feed 2.'
    Write-Host 'No launcher, scheduled task, firewall rule, recording or DSDPlus Feed 1 configuration was changed.'
} finally {
    $HostLock.Dispose()
}
