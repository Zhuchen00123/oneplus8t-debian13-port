#Requires -Version 7.2
[CmdletBinding()]
param([Parameter(Mandatory)][string]$ArtifactDirectory)
$ErrorActionPreference = 'Stop'
$project = Split-Path $PSScriptRoot -Parent
$source = [System.IO.Path]::GetFullPath($ArtifactDirectory)
$artifactPrefix = [System.IO.Path]::GetFullPath((Join-Path $project 'artifacts')).TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
if (-not $source.StartsWith($artifactPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Artifact directory must be inside this project artifacts directory.'
}
$sourceManifest = Join-Path $source 'kernel-manifest.json'
$manifestBytes = [System.IO.File]::ReadAllBytes($sourceManifest)
$manifest = [System.Text.Encoding]::UTF8.GetString($manifestBytes) | ConvertFrom-Json -AsHashtable
foreach ($name in $manifest.files_sha256.Keys) {
    if ($name -notmatch '^[A-Za-z0-9._-]+$' -or $name -in @('.', '..')) { throw "Invalid artifact filename: $name" }
    $file = Join-Path $source $name
    if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() -ne $manifest.files_sha256[$name]) {
        throw "Source artifact hash mismatch: $name"
    }
}
$destination = Join-Path $project ('backups/build-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
New-Item -ItemType Directory -Path $destination | Out-Null
$names = @($manifest.files_sha256.Keys)
if (Test-Path -LiteralPath (Join-Path $source 'kernel-build.log')) { $names += 'kernel-build.log' }
foreach ($name in $names) {
    Copy-Item -LiteralPath (Join-Path $source $name) -Destination (Join-Path $destination $name)
    if ($manifest.files_sha256.ContainsKey($name)) {
        $before = $manifest.files_sha256[$name]
    } else {
        $before = (Get-FileHash -LiteralPath (Join-Path $source $name) -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    $after = (Get-FileHash -LiteralPath (Join-Path $destination $name) -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($before -ne $after) { throw "Backup copy hash mismatch: $name" }
}
# Save the exact manifest originally validated, even if a rebuild updates its source.
[System.IO.File]::WriteAllBytes((Join-Path $destination 'kernel-manifest.json'), $manifestBytes)
$backupManifest = [ordered]@{
    CreatedAt = [DateTimeOffset]::Now.ToString('o')
    SourceDirectory = $source
    Scope = 'Compiled kernel, matching DTB/modules/config/build metadata; no phone partitions'
    HardwareValidated = $false
    Files = @(Get-ChildItem -LiteralPath $destination -File | ForEach-Object {
        [pscustomobject]@{ Name = $_.Name; Bytes = $_.Length; Sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant() }
    })
}
$backupManifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $destination 'backup-manifest.json') -Encoding utf8
Write-Host "Build artifacts copied and verified: $destination"
