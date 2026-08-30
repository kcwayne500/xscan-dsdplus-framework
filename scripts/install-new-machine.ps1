[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory = $true)]
    [ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })]
    [string]$DsdPlusArchive,
    [string]$DsdPlusRoot = 'C:\DSDPlusFastLane',
    [string]$DependencyRoot = (Join-Path $env:LOCALAPPDATA 'Programs\XScanDependencies'),
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'Programs\XScan'),
    [string]$StateRoot = (Join-Path $env:LOCALAPPDATA 'XScan'),
    [string]$PublicUrl = '',
    [string]$ExpectedSha256 = '',
    [string]$PythonExe = '',
    [switch]$Cutover,
    [switch]$SkipDownloads,
    [switch]$SkipVbCableCheck
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$SetupScript = Join-Path $ProjectRoot 'setup.ps1'
$ArchiveFull = [IO.Path]::GetFullPath($DsdPlusArchive)
$DsdRootFull = [IO.Path]::GetFullPath($DsdPlusRoot)

function Test-DsdPlusRoot {
    param([Parameter(Mandatory = $true)][string]$Path)
    return (
        (Test-Path -LiteralPath (Join-Path $Path 'DSDPlus.exe') -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $Path 'FMP24.exe') -PathType Leaf)
    )
}

if ($env:OS -ne 'Windows_NT') { throw 'This installer supports Windows only.' }
if (-not [Environment]::Is64BitOperatingSystem) { throw '64-bit Windows is required.' }
if (-not (Test-Path -LiteralPath $SetupScript -PathType Leaf)) { throw "Missing setup script: $SetupScript" }
if ($PublicUrl -and $PublicUrl -notmatch '^https://') { throw 'PublicUrl must be empty or begin with https://.' }

$ActualHash = (Get-FileHash -LiteralPath $ArchiveFull -Algorithm SHA256).Hash.ToUpperInvariant()
Write-Host "DSDPlus archive SHA-256: $ActualHash"
if ($ExpectedSha256) {
    $Expected = ($ExpectedSha256 -replace '[^0-9A-Fa-f]', '').ToUpperInvariant()
    if ($Expected.Length -ne 64) { throw 'ExpectedSha256 must contain exactly 64 hexadecimal characters.' }
    if ($Expected -ne $ActualHash) { throw 'The DSDPlus archive SHA-256 does not match ExpectedSha256.' }
}

$HaveDsdPlus = Test-DsdPlusRoot -Path $DsdRootFull
if (-not $HaveDsdPlus) {
    if (Test-Path -LiteralPath $DsdRootFull) {
        $Existing = @(Get-ChildItem -LiteralPath $DsdRootFull -Force -ErrorAction Stop)
        if ($Existing.Count -gt 0) {
            throw "Refusing to merge into incomplete non-empty DSDPlus directory: $DsdRootFull. Move it aside and rerun."
        }
    }

    $TempRoot = Join-Path ([IO.Path]::GetTempPath()) ('xscan-dsdplus-import-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $TempRoot | Out-Null
    try {
        Expand-Archive -LiteralPath $ArchiveFull -DestinationPath $TempRoot
        $DsdExe = @(Get-ChildItem -LiteralPath $TempRoot -Filter DSDPlus.exe -File -Recurse)
        $FmpExe = @(Get-ChildItem -LiteralPath $TempRoot -Filter FMP24.exe -File -Recurse)
        if ($DsdExe.Count -ne 1 -or $FmpExe.Count -ne 1) {
            throw 'Archive must contain exactly one DSDPlus.exe and one FMP24.exe.'
        }
        $SourceRoot = $DsdExe[0].Directory.FullName
        if (-not [string]::Equals($SourceRoot, $FmpExe[0].Directory.FullName, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'DSDPlus.exe and FMP24.exe are not in the same archive directory.'
        }
        if ($PSCmdlet.ShouldProcess($DsdRootFull, "Restore DSDPlus files from $ArchiveFull")) {
            New-Item -ItemType Directory -Force -Path $DsdRootFull | Out-Null
            Copy-Item -Path (Join-Path $SourceRoot '*') -Destination $DsdRootFull -Recurse -Force
        }
    } finally {
        $TempBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
        $ResolvedTemp = [IO.Path]::GetFullPath($TempRoot)
        if ($ResolvedTemp.StartsWith($TempBase, [StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -LiteralPath $ResolvedTemp -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

if ($WhatIfPreference) {
    Write-Host 'WhatIf validation completed. setup.ps1 was not run.'
    return
}
if (-not (Test-DsdPlusRoot -Path $DsdRootFull)) {
    throw "DSDPlus restore did not produce DSDPlus.exe and FMP24.exe in $DsdRootFull."
}

$SetupArguments = @{
    DsdPlusRoot = $DsdRootFull
    DependencyRoot = $DependencyRoot
    InstallRoot = $InstallRoot
    StateRoot = $StateRoot
    PublicUrl = $PublicUrl
}
if ($PythonExe) { $SetupArguments.PythonExe = $PythonExe }
if ($Cutover) { $SetupArguments.Cutover = $true }
if ($SkipDownloads) { $SetupArguments.SkipDownloads = $true }
if ($SkipVbCableCheck) { $SetupArguments.SkipVbCableCheck = $true }

& $SetupScript @SetupArguments
if (-not $?) { throw 'setup.ps1 failed.' }

Write-Host ''
Write-Host 'New-machine installation pass completed.'
Write-Host "DSDPlus: $DsdRootFull"
Write-Host "XScan state: $StateRoot"
$ValidationSuffix = if ($Cutover) { ' -Cutover' } else { '' }
Write-Host "Next check: .\scripts\test-new-machine.ps1$ValidationSuffix"
