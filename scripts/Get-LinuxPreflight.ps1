#Requires -Version 7.2
[CmdletBinding()]
param([Parameter(Mandatory)][ValidatePattern('^[A-Za-z0-9._:-]+$')][string]$Serial)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Process-Helpers.ps1')
Assert-AdbDevice -Serial $Serial -ExpectedModel KB2000
$values = [ordered]@{}
foreach ($name in @('ro.product.model','ro.product.board','ro.build.display.id','ro.boot.slot_suffix','ro.boot.vbmeta.device_state','ro.boot.verifiedbootstate')) {
    $r = Invoke-AdbRead -Serial $Serial -Command ('getprop '+$name)
    if ($r.ExitCode -ne 0) { throw 'Read-only device preflight failed' }
    $values[$name] = $r.Stdout.Trim()
}
foreach ($query in @(@('identity','id'),@('battery','dumpsys battery'),@('su_path','command -v su || true'))) {
    $r = Invoke-AdbRead -Serial $Serial -Command $query[1]
    if ($r.ExitCode -ne 0) { throw 'Read-only device preflight failed' }
    $values[$query[0]] = $r.Stdout.Trim()
}
$repo = Split-Path $PSScriptRoot -Parent
$path = Join-Path $repo ('local/linux-server-preflight-'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'.json')
[ordered]@{ time=[DateTimeOffset]::Now.ToString('o'); serial=$Serial; reads=$values; phone_written=$false } |
    ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $path -Encoding utf8
# Emit only a compact summary, never the serial or raw device logs.
[ordered]@{
    model=$values['ro.product.model']; build=$values['ro.build.display.id']; slot=$values['ro.boot.slot_suffix']
    bootloader=$values['ro.boot.vbmeta.device_state']; verified_boot=$values['ro.boot.verifiedbootstate']
    shell_uid_2000=($values.identity -match '^uid=2000\('); temporary_su_present=([bool]$values.su_path)
    battery_level=([regex]::Match($values.battery,'(?m)^\s*level:\s*(\d+)').Groups[1].Value)
    phone_written=$false; private_log_saved=$true
} | ConvertTo-Json
