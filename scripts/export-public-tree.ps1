[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$Destination)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$DestinationFull = [IO.Path]::GetFullPath($Destination)
$ProjectFull = [IO.Path]::GetFullPath($ProjectRoot)
if ($DestinationFull.StartsWith($ProjectFull + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Destination must be outside the source repository.'
}
if (Test-Path -LiteralPath $DestinationFull) {
    if (@(Get-ChildItem -LiteralPath $DestinationFull -Force).Count -gt 0) { throw "Destination is not empty: $DestinationFull" }
} else {
    New-Item -ItemType Directory -Path $DestinationFull | Out-Null
}

Push-Location $ProjectRoot
try {
    $Dirty = & git status --porcelain
    if ($Dirty) { throw 'Commit or stash all changes before exporting. The export contains HEAD only.' }
    & (Join-Path $ProjectRoot 'scripts\public-release-audit.ps1')
    $Archive = Join-Path ([IO.Path]::GetTempPath()) ("xscan-public-" + [guid]::NewGuid().ToString('N') + '.zip')
    try {
        & git archive --format=zip --output=$Archive HEAD
        if ($LASTEXITCODE -ne 0) { throw 'git archive failed.' }
        Expand-Archive -LiteralPath $Archive -DestinationPath $DestinationFull
    } finally {
        if (Test-Path -LiteralPath $Archive) { Remove-Item -LiteralPath $Archive -Force }
    }
} finally {
    Pop-Location
}
Write-Host "Clean source tree exported to $DestinationFull"
Write-Host 'Review it, run a secret scanner, then initialize a new Git repository there.'
