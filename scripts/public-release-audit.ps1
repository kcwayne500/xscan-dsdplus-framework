[CmdletBinding()]
param([switch]$IncludeHistory)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Push-Location $ProjectRoot
try {
    $Failures = [Collections.Generic.List[string]]::new()
    $Tracked = @(& git ls-files)
    if ($LASTEXITCODE -ne 0) { throw 'git ls-files failed.' }
    $BlockedPaths = $Tracked | Where-Object {
        $_ -match '(?i)(^|/)(runtime|recordings|downloads|secrets|\.codex-[^/]*)(/|$)' -or
        $_ -match '(?i)\.(exe|msi|apk|pfx|p12|pem|key|db|sqlite|wav|mp3|flac|zip|7z|rar)$'
    }
    foreach ($Path in $BlockedPaths) { $Failures.Add("blocked tracked path: $Path") }

    $TextFiles = $Tracked | Where-Object { $_ -notmatch '(?i)\.(png|ico|jpg|jpeg|gif|jar)$' }
    foreach ($Path in $TextFiles) {
        if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { continue }
        $Content = Get-Content -LiteralPath $Path -Raw -ErrorAction SilentlyContinue
        if ($Content -match '(?i)[a-z0-9-]+\.cc-group\.org') { $Failures.Add("private deployment hostname: $Path") }
        if ($Content -match 'C:\\Users\\Root') { $Failures.Add("private user path: $Path") }
        if ($Content -match '-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----') { $Failures.Add("private key material: $Path") }
    }

    if (-not (Test-Path -LiteralPath 'LICENSE' -PathType Leaf)) { $Failures.Add('LICENSE is missing') }
    if (-not (Test-Path -LiteralPath 'THIRD_PARTY_NOTICES.md' -PathType Leaf)) { $Failures.Add('THIRD_PARTY_NOTICES.md is missing') }

    if ($IncludeHistory) {
        $HistoryPaths = @(& git log --all --pretty=format: --name-only | Where-Object { $_ } | Sort-Object -Unique)
        $BlockedHistory = $HistoryPaths | Where-Object {
            $_ -match '(?i)(^|/)(runtime/(dsdplus|ffmpeg|mediamtx)|recordings|secrets|\.codex-appserver-schema-tmp)(/|$)' -or
            $_ -match '(?i)(DSDPlus|FMP24|ffmpeg|mediamtx)\.exe$' -or
            $_ -match '(?i)\.(pfx|p12|pem|key|db|sqlite|wav|mp3|flac)$'
        }
        foreach ($Path in $BlockedHistory) { $Failures.Add("blocked path exists in Git history: $Path") }
    }

    if ($Failures.Count -gt 0) {
        $Failures | Sort-Object -Unique | ForEach-Object { Write-Host "ERROR: $_" -ForegroundColor Red }
        throw "Public-release audit failed with $($Failures.Count) finding(s)."
    }
    Write-Host 'Public-release audit passed.'
} finally {
    Pop-Location
}
