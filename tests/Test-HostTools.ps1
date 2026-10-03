#Requires -Version 7.2
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../scripts/Process-Helpers.ps1')
$project = Split-Path $PSScriptRoot -Parent
$testRoot = Join-Path $project ('local/tool-test-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
New-Item -ItemType Directory -Path $testRoot | Out-Null

# Recovery images must preserve every byte and keep stderr separate.
$writer = Join-Path $testRoot 'write-bytes.ps1'
@'
$bytes = [byte[]]::new(4096)
for ($index = 0; $index -lt $bytes.Length; $index++) { $bytes[$index] = $index % 256 }
$stream = [Console]::OpenStandardOutput()
$stream.Write($bytes, 0, $bytes.Length)
$stream.Flush()
[Console]::Error.Write('diagnostic-only')
'@ | Set-Content -LiteralPath $writer -Encoding utf8
$binary = Join-Path $testRoot 'captured.bin'
$result = Invoke-CapturedProcess -FilePath (Get-Command pwsh).Source `
    -ArgumentList @('-NoProfile', '-File', $writer) -BinaryDestination $binary
$expected = [byte[]]::new(4096)
for ($index = 0; $index -lt $expected.Length; $index++) { $expected[$index] = $index % 256 }
$expectedHash = [Convert]::ToHexString([System.Security.Cryptography.SHA256]::HashData($expected))
if ($result.ExitCode -ne 0 -or (Get-Item -LiteralPath $binary).Length -ne 4096 -or
    (Get-FileHash -LiteralPath $binary -Algorithm SHA256).Hash -ne $expectedHash -or
    $result.Stderr -ne 'diagnostic-only') { throw 'Binary stream integrity check failed.' }
Write-Host 'PASS: binary byte count, SHA-256, stderr separation'

$sleeper = Join-Path $testRoot 'sleep.ps1'
'Start-Sleep -Seconds 10' | Set-Content -LiteralPath $sleeper -Encoding utf8
$timedOut = $false
try {
    Invoke-CapturedProcess -FilePath (Get-Command pwsh).Source `
        -ArgumentList @('-NoProfile', '-File', $sleeper) -TimeoutSeconds 1 | Out-Null
} catch { $timedOut = $_.Exception.Message -match 'timed out' }
if (-not $timedOut) { throw 'Process timeout guard failed.' }
Write-Host 'PASS: process timeout'

$fixture = Join-Path $testRoot 'fixture'
New-Item -ItemType Directory -Path $fixture | Out-Null
& git -C $fixture init --initial-branch=main | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Cannot initialize fixture repository.' }
"/backups/`n/ignored/`n*.img" | Set-Content -LiteralPath (Join-Path $fixture '.gitignore') -Encoding utf8
'committed contents' | Set-Content -LiteralPath (Join-Path $fixture 'tracked.txt') -Encoding utf8
Invoke-CheckedGit -Repository $fixture -GitArguments @('add', '.gitignore', 'tracked.txt') | Out-Null
Invoke-CheckedGit -Repository $fixture -GitArguments @('-c', 'user.name=Codex', '-c', 'user.email=codex@localhost',
    'commit', '-m', 'test fixture') | Out-Null
$fixtureHead = Invoke-CheckedGit -Repository $fixture -GitArguments @('rev-parse', 'HEAD')
'modified after commit' | Set-Content -LiteralPath (Join-Path $fixture 'tracked.txt') -Encoding utf8
'未提交资料' | Set-Content -LiteralPath (Join-Path $fixture '新文件.txt') -Encoding utf8
New-Item -ItemType Directory -Path (Join-Path $fixture 'ignored') | Out-Null
'dummy ignored data' | Set-Content -LiteralPath (Join-Path $fixture 'ignored/dummy.txt') -Encoding utf8
& (Join-Path $project 'scripts/Backup-Project.ps1') -Repository $fixture
$backup = Get-ChildItem -LiteralPath (Join-Path $fixture 'backups') -Directory | Select-Object -First 1
$manifest = Get-Content -LiteralPath (Join-Path $backup.FullName 'manifest.json') -Raw | ConvertFrom-Json
foreach ($item in $manifest.Files) {
    if ((Get-FileHash -LiteralPath (Join-Path $backup.FullName $item.Name) -Algorithm SHA256).Hash.ToLowerInvariant() -ne $item.Sha256) {
        throw "Backup artifact hash mismatch: $($item.Name)"
    }
}
$zip = [System.IO.Compression.ZipFile]::OpenRead((Join-Path $backup.FullName 'working-tree.zip'))
try {
    $names = @($zip.Entries.FullName)
    if ('新文件.txt' -notin $names -or 'tracked.txt' -notin $names -or ($names -match '^ignored/')) {
        throw 'ZIP omitted working files or included ignored data.'
    }
    foreach ($item in $manifest.WorkingFiles) {
        $entry = $zip.GetEntry($item.Name)
        $stream = $entry.Open()
        $sha = [System.Security.Cryptography.SHA256]::Create()
        try { $actual = [Convert]::ToHexString($sha.ComputeHash($stream)).ToLowerInvariant() }
        finally { $stream.Dispose(); $sha.Dispose() }
        if ($actual -ne $item.Sha256) { throw "Working ZIP file differs: $($item.Name)" }
    }
} finally { $zip.Dispose() }
$restore = Join-Path $testRoot 'restored'
& git clone (Join-Path $backup.FullName 'project.bundle') $restore
if ($LASTEXITCODE -ne 0) { throw 'Bundle restoration failed.' }
if ((Invoke-CheckedGit -Repository $restore -GitArguments @('rev-parse', 'HEAD')) -ne $fixtureHead) {
    throw 'Restored commit differs from fixture.'
}
if ((Get-Content -LiteralPath (Join-Path $restore 'tracked.txt') -Raw).Trim() -ne 'committed contents') {
    throw 'Bundle did not restore committed content.'
}
Write-Host 'PASS: backup hashes, modified/untracked/Unicode files, ignored-file exclusion, bundle clone restoration'
Write-Host "All host tool checks passed. Evidence: $testRoot"
