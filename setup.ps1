[CmdletBinding()]
param(
    [switch]$Cutover,
    [switch]$SkipDownloads,
    [switch]$SkipVbCableCheck,
    [string]$DsdPlusRoot = (Join-Path $env:LOCALAPPDATA 'Programs\DSDPlus'),
    [string]$DependencyRoot = (Join-Path $env:LOCALAPPDATA 'Programs\XScanDependencies'),
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'Programs\XScan'),
    [string]$StateRoot = (Join-Path $env:LOCALAPPDATA 'XScan'),
    [string]$PublicUrl = '',
    [string]$PythonExe = ''
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$V2Root = Join-Path $ProjectRoot 'v2'
$DsdPlusUrl = 'https://www.dsdplus.com/dsdplusuploads/PublicRelease/DSDPlusFull.zip'
$FfmpegUrl = 'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-lgpl.zip'
$VbCableUrl = 'https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack45.zip'

function Invoke-Checked {
    param([Parameter(Mandatory = $true)][scriptblock]$Command, [Parameter(Mandatory = $true)][string]$Description)
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$Description failed with exit code $LASTEXITCODE." }
}

function Save-Download {
    param([Parameter(Mandatory = $true)][string]$Url, [Parameter(Mandatory = $true)][string]$Destination)
    Write-Host "Downloading $Url"
    Invoke-WebRequest -Uri $Url -OutFile $Destination -UseBasicParsing
    if (-not (Test-Path -LiteralPath $Destination -PathType Leaf)) { throw "Download failed: $Url" }
}

function Expand-IntoEmptyDirectory {
    param([Parameter(Mandatory = $true)][string]$Archive, [Parameter(Mandatory = $true)][string]$Destination)
    if (Test-Path -LiteralPath $Destination) {
        $Existing = @(Get-ChildItem -LiteralPath $Destination -Force)
        if ($Existing.Count -gt 0) { throw "Refusing to extract into non-empty directory: $Destination" }
    } else {
        New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    }
    Expand-Archive -LiteralPath $Archive -DestinationPath $Destination -Force
}

if ($env:OS -ne 'Windows_NT') { throw 'XScan currently supports Windows only.' }
if ([Environment]::Is64BitOperatingSystem -ne $true) { throw 'XScan requires 64-bit Windows.' }
if ($PublicUrl -and $PublicUrl -notmatch '^https://') { throw 'PublicUrl must be empty or begin with https://.' }

if (-not $PythonExe) {
    $Launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($Launcher) {
        $PythonExe = (& $Launcher.Source -3.12 -c 'import sys; print(sys.executable)' 2>$null | Select-Object -Last 1)
    }
}
if (-not $PythonExe -or -not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    $Winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $Winget) { throw 'Python 3.12 is required. Install it from python.org, then rerun setup.ps1 -PythonExe C:\path\python.exe.' }
    Write-Host 'Installing Python 3.12 with winget...'
    Invoke-Checked -Description 'Python installation' -Command {
        & $Winget.Source install --id Python.Python.3.12 --exact --accept-package-agreements --accept-source-agreements --silent
    }
    $PythonExe = Get-ChildItem (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312') -Filter python.exe -File -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName
}
if (-not $PythonExe -or -not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) { throw 'Python 3.12 could not be located.' }

New-Item -ItemType Directory -Force -Path $DependencyRoot | Out-Null
$FfmpegExe = Get-ChildItem $DependencyRoot -Filter ffmpeg.exe -File -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName
$MediaMtxExe = Get-ChildItem $DependencyRoot -Filter mediamtx.exe -File -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName
$HaveDsdPlus = (Test-Path -LiteralPath (Join-Path $DsdPlusRoot 'DSDPlus.exe')) -and (Test-Path -LiteralPath (Join-Path $DsdPlusRoot 'FMP24.exe'))

if (-not $SkipDownloads -and (-not $HaveDsdPlus -or -not $FfmpegExe -or -not $MediaMtxExe)) {
    $TempRoot = Join-Path ([IO.Path]::GetTempPath()) ("xscan-setup-" + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $TempRoot | Out-Null
    try {
        if (-not $HaveDsdPlus) {
            $DsdArchive = Join-Path $TempRoot 'DSDPlusFull.zip'
            Save-Download -Url $DsdPlusUrl -Destination $DsdArchive
            Expand-IntoEmptyDirectory -Archive $DsdArchive -Destination $DsdPlusRoot
            $HaveDsdPlus = (Test-Path -LiteralPath (Join-Path $DsdPlusRoot 'DSDPlus.exe')) -and (Test-Path -LiteralPath (Join-Path $DsdPlusRoot 'FMP24.exe'))
            if (-not $HaveDsdPlus) { throw 'The official DSDPlus package did not contain DSDPlus.exe and FMP24.exe at its root.' }
        }
        if (-not $FfmpegExe) {
            $FfmpegArchive = Join-Path $TempRoot 'ffmpeg.zip'
            $FfmpegRoot = Join-Path $DependencyRoot 'ffmpeg'
            Save-Download -Url $FfmpegUrl -Destination $FfmpegArchive
            Expand-IntoEmptyDirectory -Archive $FfmpegArchive -Destination $FfmpegRoot
            $FfmpegExe = Get-ChildItem $FfmpegRoot -Filter ffmpeg.exe -File -Recurse | Select-Object -First 1 -ExpandProperty FullName
        }
        if (-not $MediaMtxExe) {
            $Release = Invoke-RestMethod -Uri 'https://api.github.com/repos/bluenviron/mediamtx/releases/latest' -Headers @{ 'User-Agent' = 'XScan-setup' }
            $Asset = $Release.assets | Where-Object { $_.name -match '_windows_amd64\.zip$' } | Select-Object -First 1
            if (-not $Asset) { throw 'The latest MediaMTX release has no Windows amd64 archive.' }
            $MediaArchive = Join-Path $TempRoot 'mediamtx.zip'
            $MediaRoot = Join-Path $DependencyRoot 'mediamtx'
            Save-Download -Url $Asset.browser_download_url -Destination $MediaArchive
            Expand-IntoEmptyDirectory -Archive $MediaArchive -Destination $MediaRoot
            $MediaMtxExe = Get-ChildItem $MediaRoot -Filter mediamtx.exe -File -Recurse | Select-Object -First 1 -ExpandProperty FullName
        }
    } finally {
        $ResolvedTemp = [IO.Path]::GetFullPath($TempRoot)
        if ($ResolvedTemp.StartsWith([IO.Path]::GetFullPath([IO.Path]::GetTempPath()), [StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -LiteralPath $ResolvedTemp -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

if (-not $HaveDsdPlus) { throw "DSDPlus and FMP24 were not found in $DsdPlusRoot. Remove -SkipDownloads or supply -DsdPlusRoot." }
if (-not $FfmpegExe) { throw "FFmpeg was not found below $DependencyRoot. Remove -SkipDownloads or install it and rerun." }
if (-not $MediaMtxExe) { throw "MediaMTX was not found below $DependencyRoot. Remove -SkipDownloads or install it and rerun." }

$VbCablePresent = @(Get-CimInstance Win32_SoundDevice -ErrorAction SilentlyContinue | Where-Object { $_.Name -match 'VB-Audio|CABLE Input|CABLE Output' }).Count -gt 0
if (-not $VbCablePresent -and -not $SkipVbCableCheck) {
    $DriverRoot = Join-Path $DependencyRoot 'VBCABLE'
    if (-not (Test-Path -LiteralPath $DriverRoot)) {
        if ($SkipDownloads) {
            Write-Warning "VB-CABLE is not installed. Download it from https://vb-audio.com/Cable/index.htm before cutover."
        } else {
            $DriverArchive = Join-Path $DependencyRoot 'VBCABLE_Driver_Pack45.zip'
            Save-Download -Url $VbCableUrl -Destination $DriverArchive
            Expand-IntoEmptyDirectory -Archive $DriverArchive -Destination $DriverRoot
        }
    }
    if ($Cutover) {
        throw "VB-CABLE is not installed. Install its official driver as administrator, reboot, then rerun setup.ps1 -Cutover. Staged path: $DriverRoot"
    }
    if (Test-Path -LiteralPath $DriverRoot) {
        Write-Warning "VB-CABLE is not installed. Its official driver was staged in $DriverRoot; install it as administrator and reboot before cutover."
    }
}

Invoke-Checked -Description 'XScan build and tests' -Command {
    & (Join-Path $V2Root 'build.ps1') -Clean -PythonExe $PythonExe
}

$InstallArguments = @{
    InstallRoot = $InstallRoot
    StateRoot = $StateRoot
    DsdPlusRoot = $DsdPlusRoot
    FfmpegExe = $FfmpegExe
    MediaMtxExe = $MediaMtxExe
    PublicUrl = $PublicUrl
}
if ($Cutover) { $InstallArguments.Cutover = $true }
& (Join-Path $V2Root 'install.ps1') @InstallArguments

Write-Host ''
Write-Host 'XScan setup is complete.'
if (-not $Cutover) { Write-Host 'This was a safe side-by-side install. After VB-CABLE and the SDR work, rerun: .\setup.ps1 -Cutover' }
Write-Host 'Open the local dashboard and create the administrator password shown on first use.'
