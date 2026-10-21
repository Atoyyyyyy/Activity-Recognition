$ErrorActionPreference = 'Stop'

$headerPath = Join-Path $PSScriptRoot 'wifi_local.h'
if (Test-Path -LiteralPath $headerPath) {
    throw 'wifi_local.h already exists; refusing to overwrite local credentials.'
}

function ConvertFrom-SecureInput {
    param([System.Security.SecureString]$Value)
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToGlobalAllocUnicode($Value)
    try {
        [Runtime.InteropServices.Marshal]::PtrToStringUni($pointer)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeGlobalAllocUnicode($pointer)
    }
}

function ConvertTo-CStringLiteral {
    param([string]$Value)
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Value)
    $builder = [System.Text.StringBuilder]::new()
    foreach ($byte in $bytes) {
        if ($byte -eq 34) { [void]$builder.Append('\"') }
        elseif ($byte -eq 92) { [void]$builder.Append('\\') }
        elseif ($byte -eq 10) { [void]$builder.Append('\n') }
        elseif ($byte -eq 13) { [void]$builder.Append('\r') }
        elseif ($byte -eq 9) { [void]$builder.Append('\t') }
        elseif ($byte -ge 32 -and $byte -le 126) { [void]$builder.Append([char]$byte) }
        else { [void]$builder.Append('\' + $byte.ToString('000')) }
    }
    '"' + $builder.ToString() + '"'
}

$ssidSecure = Read-Host 'Enter HOME Wi-Fi SSID (input hidden)' -AsSecureString
$ssid = ConvertFrom-SecureInput $ssidSecure
$ssidBytes = [System.Text.Encoding]::UTF8.GetByteCount($ssid)
if ($ssidBytes -lt 1 -or $ssidBytes -gt 32) {
    throw 'SSID must be 1 to 32 UTF-8 bytes; no header was written.'
}

Write-Host 'Security type: 1 WPA2-Personal AES (recommended), 2 WPA2 mixed, 3 WPA AES, 4 WPA TKIP, 5 Open, 6 WEP PSK, 7 WPA3-only (unsupported).'
$securityChoice = Read-Host 'Select security type [1]'
if ([string]::IsNullOrWhiteSpace($securityChoice)) { $securityChoice = '1' }
$securityMap = @{
    '1' = @{ Macro = 'SECURITY_WPA2_AES_PSK'; Name = 'WPA2-Personal AES'; NeedsPassword = $true; WPA = $true }
    '2' = @{ Macro = 'SECURITY_WPA2_MIXED_PSK'; Name = 'WPA2 mixed'; NeedsPassword = $true; WPA = $true }
    '3' = @{ Macro = 'SECURITY_WPA_AES_PSK'; Name = 'WPA AES'; NeedsPassword = $true; WPA = $true }
    '4' = @{ Macro = 'SECURITY_WPA_TKIP_PSK'; Name = 'WPA TKIP'; NeedsPassword = $true; WPA = $true }
    '5' = @{ Macro = 'SECURITY_OPEN'; Name = 'Open'; NeedsPassword = $false; WPA = $false }
    '6' = @{ Macro = 'SECURITY_WEP_PSK'; Name = 'WEP PSK'; NeedsPassword = $true; WPA = $false }
}
if ($securityChoice -eq '7') {
    throw 'RW007 v2.0.1 does not support WPA3-only networks; no header was written.'
}
if (-not $securityMap.ContainsKey($securityChoice)) {
    throw 'Choose a supported security option from 1 through 6; no header was written.'
}
$security = $securityMap[$securityChoice]

$channelText = Read-Host '2.4 GHz channel [2; laptop currently reports channel 2]'
if ([string]::IsNullOrWhiteSpace($channelText)) { $channelText = '2' }
$channel = 0
if (-not [int]::TryParse($channelText, [ref]$channel) -or $channel -lt 1 -or $channel -gt 13) {
    throw 'Channel must be a fixed 2.4 GHz channel from 1 to 13; no header was written.'
}

$hiddenText = Read-Host 'Does this access point hide its SSID? [y/N]'
$hidden = 0
if ($hiddenText -match '^(?i:y|yes)$') { $hidden = 1 }

$password = ''
if ($security.NeedsPassword) {
    $passwordSecure = Read-Host 'Enter Wi-Fi password (input hidden)' -AsSecureString
    $password = ConvertFrom-SecureInput $passwordSecure
    $passwordBytes = [System.Text.Encoding]::UTF8.GetByteCount($password)
    if ($security.WPA -and ($passwordBytes -lt 8 -or $passwordBytes -gt 31)) {
        throw 'This RW007 build accepts WPA/WPA2 passwords from 8 to 31 UTF-8 bytes; no header was written.'
    }
    if (-not $security.WPA -and $passwordBytes -gt 31) {
        throw 'This RW007 build accepts at most 31 password bytes; no header was written.'
    }
    if ($security.Name -eq 'WEP PSK' -and
        -not (($passwordBytes -eq 5 -or $passwordBytes -eq 13) -or
              ($password -match '^[0-9A-Fa-f]+$' -and ($passwordBytes -eq 10 -or $passwordBytes -eq 26)))) {
        throw 'WEP key must be 5 or 13 text bytes, or 10 or 26 hexadecimal digits; no header was written.'
    }
}

$lines = @(
    '#ifndef WIFI_LOCAL_H'
    '#define WIFI_LOCAL_H'
    ''
    '/* Local-only Wi-Fi configuration. This file is Git-ignored. */'
    ('#define WIFI_LOCAL_SSID ' + (ConvertTo-CStringLiteral $ssid))
    ('#define WIFI_LOCAL_PASSWORD ' + (ConvertTo-CStringLiteral $password))
    ('#define WIFI_LOCAL_SECURITY ' + $security.Macro)
    ('#define WIFI_LOCAL_CHANNEL ' + $channel)
    ('#define WIFI_LOCAL_HIDDEN ' + $hidden)
    ''
    '#endif'
    ''
)
[System.IO.File]::WriteAllLines($headerPath, $lines, [System.Text.UTF8Encoding]::new($false))

Write-Host 'wifi_local.h created locally. SSID and password were not echoed.'
Write-Host ("Selected network mode: {0}, channel {1}, hidden={2}." -f $security.Name, $channel, $hidden)
