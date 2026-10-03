#Requires -Version 7.2
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidatePattern('^[A-Za-z0-9._:-]+$')][string]$Serial,
    [string]$ExpectedModel = 'KB2000'
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Process-Helpers.ps1')
$project = Split-Path $PSScriptRoot -Parent
Assert-AdbDevice -Serial $Serial -ExpectedModel $ExpectedModel
$runName = 'device-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff')
$directory = Join-Path $project ('local/' + $runName)
New-Item -ItemType Directory -Path $directory | Out-Null

$properties = @('ro.product.model', 'ro.product.device', 'ro.product.vendor.device',
    'ro.product.board', 'ro.board.platform', 'ro.build.version.release',
    'ro.build.version.security_patch', 'ro.build.display.id', 'ro.boot.flash.locked',
    'ro.boot.vbmeta.device_state', 'ro.boot.verifiedbootstate', 'ro.boot.slot_suffix',
    'sys.oem_unlock_allowed', 'ro.boot.panel_err')
$values = [ordered]@{}
foreach ($name in $properties) {
    $result = Invoke-AdbRead -Serial $Serial -Command ('getprop ' + $name)
    if ($result.ExitCode -ne 0) { throw "Cannot read property $name" }
    $values[$name] = $result.Stdout.Trim()
}
$values | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $directory 'properties.json') -Encoding utf8
$queries = [ordered]@{
    'identity.txt' = 'id'
    'partitions.txt' = 'ls -l /dev/block/by-name'
    'display.txt' = 'dumpsys display'
    'surfaceflinger.txt' = 'dumpsys SurfaceFlinger'
    'display-identification.txt' = 'dumpsys SurfaceFlinger --display-id'
}
foreach ($entry in $queries.GetEnumerator()) {
    $result = Invoke-AdbRead -Serial $Serial -Command $entry.Value
    ($result.Stdout + $result.Stderr) | Set-Content -LiteralPath (Join-Path $directory $entry.Key) -Encoding utf8
}
$paths = @('/proc/devinfo/lcd', '/proc/devinfo/Screen', '/proc/devinfo/tp',
    '/sys/kernel/oplus_display/panel_id', '/sys/kernel/oplus_display/panel_serial_number',
    '/sys/class/drm/card0-DSI-1/status', '/sys/class/drm/card0-DSI-1/modes', '/proc/cmdline')
$panelResults = foreach ($path in $paths) {
    $result = Invoke-AdbRead -Serial $Serial -Command ('cat ' + $path)
    [pscustomobject]@{ Path = $path; ExitCode = $result.ExitCode; Content = $result.Stdout; Error = $result.Stderr }
}
$panelResults | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $directory 'panel-reads.json') -Encoding utf8
$manifest = [ordered]@{
    CollectedAt = [DateTimeOffset]::Now.ToString('o')
    Serial = $Serial
    Model = $values['ro.product.model']
    Bootloader = $values['ro.boot.vbmeta.device_state']
    OriginalFactoryScreen = 'unconfirmed; requires hardware evidence'
    PartitionImagesBackedUp = $false
    Files = @(Get-ChildItem -LiteralPath $directory -File | ForEach-Object {
        [pscustomobject]@{ Name = $_.Name; Sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant() }
    })
}
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $directory 'manifest.json') -Encoding utf8
[pscustomobject]@{ Model = $manifest.Model; Bootloader = $manifest.Bootloader; Screen = $manifest.OriginalFactoryScreen; EvidenceDirectory = $directory }
