#Requires -Version 7.2
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidatePattern('^[A-Za-z0-9._:-]+$')][string]$Serial,
    [Parameter(Mandatory)][ValidatePattern('^[A-Za-z0-9_]+$')][string[]]$Partition,
    [string]$ExpectedModel = 'KB2000',
    [ValidateSet('Direct', 'Su')][string]$RootMode = 'Direct',
    [ValidateRange(30, 7200)][int]$TimeoutSeconds = 3600,
    [switch]$Plan
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Process-Helpers.ps1')
$project = Split-Path $PSScriptRoot -Parent
Assert-AdbDevice -Serial $Serial -ExpectedModel $ExpectedModel
if (@($Partition | Sort-Object -Unique).Count -ne $Partition.Count) { throw 'Duplicate partition names.' }

function Read-Privileged([string]$Command) {
    # Partition names are validated above; commands contain no user shell code.
    if ($RootMode -eq 'Su') { $Command = "su -c '$Command'" }
    $result = Invoke-AdbRead -Serial $Serial -Command $Command -TimeoutSeconds $TimeoutSeconds
    if ($result.ExitCode -ne 0) { throw "Read failed: $($result.Stderr.Trim())" }
    $result.Stdout.Trim()
}

if ($Plan) {
    foreach ($name in $Partition) {
        $result = Invoke-AdbRead -Serial $Serial -Command ('readlink -f /dev/block/by-name/' + $name)
        [pscustomobject]@{ Partition = $name; Path = '/dev/block/by-name/' + $name; ObservedTarget = $result.Stdout.Trim(); Mode = 'plan_only' }
    }
    return
}
$uid = Read-Privileged 'id -u'
if ($uid -ne '0') { throw "Root access required; current UID is $uid. No partition images created." }

# Preflight every partition before creating a backup directory or image.
$items = foreach ($name in $Partition) {
    $remotePath = '/dev/block/by-name/' + $name
    $target = Read-Privileged ('readlink -f ' + $remotePath)
    if ($target -notmatch '^/dev/block/[A-Za-z0-9_./-]+$') { throw "Invalid/missing block target for $name" }
    $sizeText = Read-Privileged ('blockdev --getsize64 ' + $remotePath)
    [long]$size = 0
    if (-not [long]::TryParse($sizeText, [ref]$size) -or $size -le 0) { throw "Invalid size for $name" }
    [pscustomobject]@{ Name = $name; RemotePath = $remotePath; Target = $target; Bytes = $size }
}
$totalBytes = ($items | Measure-Object -Property Bytes -Sum).Sum
$drive = [System.IO.DriveInfo]::new([System.IO.Path]::GetPathRoot($project))
if ($drive.AvailableFreeSpace -lt ($totalBytes + 1GB)) { throw 'Insufficient free space for requested partitions.' }
$directory = Join-Path $project ('backups/partitions-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
New-Item -ItemType Directory -Path $directory | Out-Null
$manifest = [ordered]@{
    StartedAt = [DateTimeOffset]::Now.ToString('o'); Serial = $Serial; Model = $ExpectedModel
    RootMode = $RootMode; Status = 'in_progress'; ConsistentAcrossPartitions = 'not guaranteed; use quiescent recovery'
    Partitions = @()
}
$manifestFile = Join-Path $directory 'manifest.json'
function Save-Manifest { $manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $manifestFile -Encoding utf8 }
Save-Manifest
try {
    foreach ($item in $items) {
        Write-Host "Reading $($item.Name) ($($item.Bytes) bytes)"
        $before = Read-Privileged ('sha256sum ' + $item.RemotePath)
        $beforeHash = ($before -split '\s+')[0].ToLowerInvariant()
        if ($beforeHash -notmatch '^[a-f0-9]{64}$') { throw "Invalid device hash for $($item.Name)" }
        $partial = Join-Path $directory ($item.Name + '.img.partial')
        $command = 'cat ' + $item.RemotePath
        if ($RootMode -eq 'Su') { $command = "su -c '$command'" }
        $result = Invoke-CapturedProcess -FilePath (Get-Command adb -ErrorAction Stop).Source `
            -ArgumentList @('-s', $Serial, 'exec-out', $command) -BinaryDestination $partial -TimeoutSeconds $TimeoutSeconds
        if ($result.ExitCode -ne 0) { throw "Read failed: $($result.Stderr.Trim())" }
        if ((Get-Item -LiteralPath $partial).Length -ne $item.Bytes) { throw "Byte count mismatch for $($item.Name)" }
        $localHash = (Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash.ToLowerInvariant()
        $after = Read-Privileged ('sha256sum ' + $item.RemotePath)
        $afterHash = ($after -split '\s+')[0].ToLowerInvariant()
        if ($beforeHash -ne $localHash -or $afterHash -ne $localHash) { throw "Hash mismatch or live partition changed: $($item.Name)" }
        $complete = Join-Path $directory ($item.Name + '.img')
        Move-Item -LiteralPath $partial -Destination $complete
        $manifest.Partitions += [pscustomobject]@{
            Name = $item.Name; Target = $item.Target; Bytes = $item.Bytes; Sha256 = $localHash; Verified = $true
        }
        Save-Manifest
    }
    $manifest.Status = 'verified_requested_partitions'
    $manifest.CompletedAt = [DateTimeOffset]::Now.ToString('o')
    Save-Manifest
    Write-Host "Verified requested partitions: $directory"
} catch {
    $manifest.Status = 'failed'
    $manifest.Error = $_.Exception.Message
    Save-Manifest
    throw
}
