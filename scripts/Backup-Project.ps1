#Requires -Version 7.2
[CmdletBinding()]
param([string]$Repository = (Split-Path $PSScriptRoot -Parent))
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Process-Helpers.ps1')
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$repositoryPath = [System.IO.Path]::GetFullPath($Repository)
$head = Invoke-CheckedGit -Repository $repositoryPath -GitArguments @('rev-parse', '--verify', 'HEAD')
$names = Invoke-CheckedGit -Repository $repositoryPath -GitArguments @('ls-files', '--cached', '--others', '--exclude-standard', '-z')
$files = @($names.Split([char]0, [StringSplitOptions]::RemoveEmptyEntries) | Sort-Object -Unique)
foreach ($name in $files) {
    if ($name -match '^(sources|local|firmware|backups|artifacts|downloads|build)/' -or
        $name -match '(?i)(\.img|\.mbn|\.mdt|\.pem|\.key|\.bundle)$' -or
        $name -match '(^|/)\.env($|\.)') { throw "Sensitive/generated file would be snapshotted: $name" }
}
$directory = Join-Path $repositoryPath ('backups/project-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
New-Item -ItemType Directory -Path $directory | Out-Null
$bundle = Join-Path $directory 'project.bundle'
Invoke-CheckedGit -Repository $repositoryPath -GitArguments @('bundle', 'create', $bundle, '--all') -TimeoutSeconds 300 | Out-Null
Invoke-CheckedGit -Repository $repositoryPath -GitArguments @('bundle', 'verify', $bundle) | Out-Null
$status = Invoke-CheckedGit -Repository $repositoryPath -GitArguments @('status', '--short', '--branch')
$status | Set-Content -LiteralPath (Join-Path $directory 'status.txt') -Encoding utf8
$zipPath = Join-Path $directory 'working-tree.zip'
$zipStream = [System.IO.File]::Open($zipPath, [System.IO.FileMode]::CreateNew)
$archive = [System.IO.Compression.ZipArchive]::new($zipStream, [System.IO.Compression.ZipArchiveMode]::Create)
$savedFiles = @()
try {
    foreach ($name in $files) {
        $file = [System.IO.Path]::GetFullPath((Join-Path $repositoryPath $name))
        $prefix = $repositoryPath.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
        if (-not $file.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { throw "Path escapes repository: $name" }
        if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { continue }
        if (((Get-Item -LiteralPath $file).Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Symlink/reparse point needs separate review: $name"
        }
        [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($archive, $file, $name,
            [System.IO.Compression.CompressionLevel]::Optimal) | Out-Null
        $savedFiles += [pscustomobject]@{ Name = $name; Sha256 = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() }
    }
} finally {
    $archive.Dispose()
    $zipStream.Dispose()
}
$manifest = [ordered]@{
    CreatedAt = [DateTimeOffset]::Now.ToString('o'); Head = $head
    Scope = 'Git refs plus current tracked and nonignored file contents; excludes private/generated directories'
    WorkingFiles = $savedFiles
    Files = @(Get-ChildItem -LiteralPath $directory -File | ForEach-Object {
        [pscustomobject]@{ Name = $_.Name; Bytes = $_.Length; Sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant() }
    })
}
$manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $directory 'manifest.json') -Encoding utf8
Write-Host "Project backup created and bundle verified: $directory"
