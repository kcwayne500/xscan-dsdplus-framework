[CmdletBinding()]
param(
    [switch]$SkipTests,
    [string]$PublicUrl = $env:XSCAN_PUBLIC_URL
)

$ErrorActionPreference = 'Stop'
if (-not $PublicUrl -or $PublicUrl -notmatch '^https://') {
    throw 'Specify -PublicUrl https://scanner.example.com (or set XSCAN_PUBLIC_URL) before building the Android app.'
}
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$AndroidRoot = Join-Path $ProjectRoot 'android'
$ToolsRoot = Join-Path $env:LOCALAPPDATA 'XScan\android-tools'
$SdkRoot = Join-Path $ToolsRoot 'sdk'
$JavaHome = Get-ChildItem (Join-Path $ToolsRoot 'jdk') -Directory | Where-Object { Test-Path (Join-Path $_.FullName 'bin\java.exe') } | Select-Object -First 1 -ExpandProperty FullName
if (-not $JavaHome) { throw 'XScan Android JDK 17 is not installed.' }
if (-not (Test-Path (Join-Path $SdkRoot 'platforms\android-36\android.jar'))) { throw 'Android API 36 is not installed.' }

$Secrets = Join-Path $env:LOCALAPPDATA 'XScan\secrets'
$KeyStore = Join-Path $Secrets 'android-release.jks'
$PasswordFile = Join-Path $Secrets 'android-release-password.dpapi'
$RecoveryFile = Join-Path $Secrets 'android-signing-recovery.txt'
New-Item -ItemType Directory -Force -Path $Secrets | Out-Null
if (-not (Test-Path -LiteralPath $PasswordFile)) {
    $Bytes = New-Object byte[] 36
    [Security.Cryptography.RandomNumberGenerator]::Fill($Bytes)
    $Password = [Convert]::ToBase64String($Bytes).Replace('+','A').Replace('/','B').TrimEnd('=')
    ConvertTo-SecureString $Password -AsPlainText -Force | ConvertFrom-SecureString | Set-Content -LiteralPath $PasswordFile -Encoding ASCII
} else {
    $Password = [Net.NetworkCredential]::new('',(Get-Content -LiteralPath $PasswordFile | ConvertTo-SecureString)).Password
}
if (-not (Test-Path -LiteralPath $RecoveryFile)) {
    [IO.File]::WriteAllText($RecoveryFile, "XScan Android signing password`r`n$Password`r`n", [Text.UTF8Encoding]::new($false))
    & icacls.exe $RecoveryFile /inheritance:r /grant:r "$env:USERNAME`:(F)" 'SYSTEM:(F)' | Out-Null
}

$env:JAVA_HOME = $JavaHome
$env:ANDROID_HOME = $SdkRoot
$env:ANDROID_SDK_ROOT = $SdkRoot
$env:XSCAN_ANDROID_KEYSTORE = $KeyStore
$env:XSCAN_ANDROID_STORE_PASSWORD = $Password
$env:XSCAN_ANDROID_KEY_PASSWORD = $Password
$env:XSCAN_ANDROID_KEY_ALIAS = 'xscan-release'
[IO.File]::WriteAllText((Join-Path $AndroidRoot 'local.properties'), "sdk.dir=$($SdkRoot.Replace('\','\\'))`n", [Text.UTF8Encoding]::new($false))

if (-not (Test-Path -LiteralPath $KeyStore)) {
    & (Join-Path $JavaHome 'bin\keytool.exe') -genkeypair -keystore $KeyStore -storepass $Password -keypass $Password -alias xscan-release -keyalg RSA -keysize 4096 -validity 10000 -dname 'CN=XScan Radio Console, OU=Private Distribution, O=XScan, C=US'
    if ($LASTEXITCODE -ne 0) { throw 'Could not generate the persistent Android signing key.' }
}

Push-Location $AndroidRoot
try {
    if (-not $SkipTests) { & .\gradlew.bat test --no-daemon "-PxscanPublicUrl=$PublicUrl"; if ($LASTEXITCODE -ne 0) { throw 'Android tests failed.' } }
    & .\gradlew.bat assembleRelease --no-daemon "-PxscanPublicUrl=$PublicUrl"
    if ($LASTEXITCODE -ne 0) { throw 'Android release build failed.' }
} finally { Pop-Location }

$Apk = Join-Path $AndroidRoot 'app\build\outputs\apk\release\app-release.apk'
$Destination = Join-Path $ProjectRoot 'xscan\web\downloads\XScan-Android-1.0.4.apk'
if (-not (Test-Path -LiteralPath $Apk)) { throw 'Signed APK output is missing.' }
Copy-Item -LiteralPath $Apk -Destination $Destination -Force
$ApkSigner = Join-Path $SdkRoot 'build-tools\36.0.0\apksigner.bat'
& $ApkSigner verify --verbose --print-certs $Destination
if ($LASTEXITCODE -ne 0) { throw 'APK signature verification failed.' }
$Hash = (Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash.ToLowerInvariant()
Write-Host "Android APK: $Destination"
Write-Host "SHA-256: $Hash"
Write-Warning "Securely back up $KeyStore and $RecoveryFile together. Future Android updates require this same signing identity."
