$ErrorActionPreference = 'Stop'

$headerPath = Join-Path $PSScriptRoot 'mqtt_local.h'
if (Test-Path -LiteralPath $headerPath) {
    throw 'mqtt_local.h already exists; refusing to overwrite local credentials.'
}

$passwordSecure = Read-Host 'Enter Mosquitto password for rt_spark (input hidden)' -AsSecureString
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToGlobalAllocUnicode($passwordSecure)
try {
    $password = [Runtime.InteropServices.Marshal]::PtrToStringUni($pointer)
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeGlobalAllocUnicode($pointer)
}

if ([string]::IsNullOrEmpty($password) -or $password.Contains("`r") -or $password.Contains("`n")) {
    $password = $null
    throw 'Password must be non-empty and single-line; no header was written.'
}

$escaped = $password.Replace('\', '\\').Replace('"', '\"')
$lines = @(
    '#ifndef MQTT_LOCAL_H'
    '#define MQTT_LOCAL_H'
    ''
    '/* Local-only broker credentials. This header is Git-ignored. */'
    '#define MQTT_LOCAL_USERNAME "rt_spark"'
    ('#define MQTT_LOCAL_PASSWORD "' + $escaped + '"')
    '#define MQTT_LOCAL_CLIENT_ID "rt-spark-stm32f407-01"'
    ''
    '#endif'
    ''
)
[System.IO.File]::WriteAllLines($headerPath, $lines, [System.Text.UTF8Encoding]::new($false))

$password = $null
$escaped = $null
Write-Host 'mqtt_local.h created locally. The password was not echoed.'
