#Requires -Version 7.2
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$BootDirectory,
    [Parameter(Mandatory)][string]$RootfsDirectory,
    [string]$Repository = (Split-Path $PSScriptRoot -Parent)
)
$ErrorActionPreference = 'Stop'
$repo = [System.IO.Path]::GetFullPath($Repository)
$inputs = [System.Collections.Generic.List[object]]::new()
function Add-VerifiedComponent {
    param([string]$Directory, [string]$ManifestName, [string]$Section)
    $root = [System.IO.Path]::GetFullPath((Resolve-Path -LiteralPath $Directory).Path)
    $manifestPath = Join-Path $root $ManifestName
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    $names = @($manifest.files_sha256.PSObject.Properties)
    if ($names.Count -eq 0) { throw "Empty component manifest: $ManifestName" }
    foreach ($item in $names) {
        $file = [System.IO.Path]::GetFullPath((Join-Path $root $item.Name))
        $prefix = $root.TrimEnd('\','/') + [System.IO.Path]::DirectorySeparatorChar
        if (-not $file.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) -or
            $item.Value -notmatch '^[a-f0-9]{64}$') { throw 'Invalid manifest path/hash' }
        $entry = Get-Item -LiteralPath $file
        if ($entry.PSIsContainer -or ($entry.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Manifest requires ordinary files' }
        if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() -ne $item.Value) { throw "Source checksum mismatch: $($item.Name)" }
        $inputs.Add([pscustomobject]@{ Source=$file; Destination="$Section/$($item.Name)"; Bytes=$entry.Length; Sha256=$item.Value })
    }
    $inputs.Add([pscustomobject]@{ Source=$manifestPath; Destination="$Section/$ManifestName";
        Bytes=(Get-Item -LiteralPath $manifestPath).Length; Sha256=(Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant() })
}
Add-VerifiedComponent -Directory $BootDirectory -ManifestName boot-manifest.json -Section rescue
Add-VerifiedComponent -Directory $RootfsDirectory -ManifestName rootfs-export-manifest.json -Section debian-base
$device = Get-Content -LiteralPath (Join-Path $repo 'config/device-kb2000.json') -Raw | ConvertFrom-Json
$stockManifestPath = Join-Path $repo $device.original_partition_backup_manifest
$stockManifest = Get-Content -LiteralPath $stockManifestPath -Raw | ConvertFrom-Json
foreach ($name in @('boot_a','dtbo_a','recovery_a')) {
    $entries = @($stockManifest.Partitions | Where-Object Name -eq $name)
    if ($entries.Count -ne 1 -or -not $entries[0].Verified) { throw "Missing verified restore input: $name" }
    $part = $entries[0]
    $file = Join-Path (Split-Path $stockManifestPath -Parent) "$name.img"
    if ((Get-Item -LiteralPath $file).Length -ne $part.Bytes -or
        (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() -ne $part.Sha256) { throw "Restore input checksum mismatch: $name" }
    $inputs.Add([pscustomobject]@{ Source=$file; Destination="stock-restore/$name.img"; Bytes=$part.Bytes; Sha256=$part.Sha256 })
}
# Validate every source before creating a destination. No device operations.
$destination = Join-Path $repo ('backups/linux-preparation-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
New-Item -ItemType Directory -Path $destination | Out-Null
foreach ($copyEntry in $inputs) {
    $target = Join-Path $destination $copyEntry.Destination
    New-Item -ItemType Directory -Path (Split-Path $target -Parent) -Force | Out-Null
    Copy-Item -LiteralPath $copyEntry.Source -Destination $target
    if ((Get-Item -LiteralPath $target).Length -ne $copyEntry.Bytes -or
        (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant() -ne $copyEntry.Sha256) { throw "Backup verification failed: $($copyEntry.Destination)" }
}
$manifest = [ordered]@{
    CreatedAt=[DateTimeOffset]::Now.ToString('o'); Status='verified_linux_preparation_snapshot'
    Scope='RAM rescue candidate + Debian base archive + original slot-a boot/DTBO/recovery restore copies; not a full device backup'
    PhoneWritten=$false; IndependentMedia=$false
    Files=@($inputs | ForEach-Object { [ordered]@{ Name=$_.Destination; Bytes=$_.Bytes; Sha256=$_.Sha256 } })
}
$manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $destination manifest.json) -Encoding utf8
$destination | Set-Content -LiteralPath (Join-Path $repo 'local/current-linux-preparation-backup.txt') -Encoding utf8
Write-Host "Verified Linux preparation snapshot: $destination ($($inputs.Count) files)"
