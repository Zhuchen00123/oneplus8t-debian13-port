#Requires -Version 7.2
[CmdletBinding()]
param([Parameter(Mandatory)][string]$CandidateDirectory)
$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$source = (Resolve-Path -LiteralPath $CandidateDirectory).Path
$manifestPath = Join-Path $source server-manifest.json
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$inputs = @()
$entries = @($manifest.files_sha256.PSObject.Properties)
if ($entries.Count -eq 0) { throw 'Candidate manifest must contain files' }
foreach ($entry in $entries) {
    $path = [IO.Path]::GetFullPath((Join-Path $source $entry.Name))
    $prefix = $source.TrimEnd('\','/') + [IO.Path]::DirectorySeparatorChar
    if (-not $path.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase) -or $entry.Value -notmatch '^[a-f0-9]{64}$') { throw 'Invalid manifest path/hash' }
    if (((Get-Item -LiteralPath $path).Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Ordinary files required' }
    if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $entry.Value) { throw 'Candidate source checksum mismatch' }
    $inputs += [pscustomobject]@{source=$path;name=('server/'+$entry.Name);sha256=$entry.Value}
}
$inputs += [pscustomobject]@{source=$manifestPath;name='server/server-manifest.json';sha256=(Get-FileHash $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant()}
$device = Get-Content (Join-Path $repo config/device-kb2000.json) -Raw | ConvertFrom-Json
$stockPath = Join-Path $repo $device.original_partition_backup_manifest
$stock = Get-Content $stockPath -Raw | ConvertFrom-Json
foreach ($name in @('boot_a','dtbo_a','recovery_a','super')) {
    $parts = @($stock.Partitions | Where-Object Name -eq $name)
    if ($parts.Count -ne 1 -or -not $parts[0].Verified) { throw 'Verified restore input missing' }
    $file = Join-Path (Split-Path $stockPath -Parent) ($name+'.img')
    if ((Get-Item $file).Length -ne $parts[0].Bytes -or (Get-FileHash $file -Algorithm SHA256).Hash.ToLowerInvariant() -ne $parts[0].Sha256) { throw 'Stock restore checksum mismatch' }
    # Reuse the already preserved original images; record their validated
    # locations rather than duplicating a 7 GiB super image every stage.
}
$dest = Join-Path $repo ('backups/server-'+(Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
New-Item -ItemType Directory -Path $dest | Out-Null
$copied = @()
foreach ($entry in $inputs) {
    $target = Join-Path $dest $entry.name
    New-Item -ItemType Directory -Path (Split-Path $target -Parent) -Force | Out-Null
    Copy-Item -LiteralPath $entry.source -Destination $target
    if ((Get-FileHash $target -Algorithm SHA256).Hash.ToLowerInvariant() -ne $entry.sha256) { throw 'Backup checksum mismatch' }
    $copied += [ordered]@{name=$entry.name;bytes=(Get-Item $target).Length;sha256=$entry.sha256}
}
[ordered]@{time=[DateTimeOffset]::Now.ToString('o');files=$copied;stock_restore_manifest=$device.original_partition_backup_manifest;
    stock_boot_dtbo_recovery_super_rehashed=$true;independent_media=$false;phone_written=$false} |
    ConvertTo-Json -Depth 6 | Set-Content (Join-Path $dest manifest.json) -Encoding utf8
Write-Host ('Verified server candidate backup: '+$dest)
