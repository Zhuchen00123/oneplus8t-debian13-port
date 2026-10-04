#Requires -Version 7.2
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Process-Helpers.ps1')
$project = Split-Path $PSScriptRoot -Parent
$lock = Get-Content -LiteralPath (Join-Path $project 'sources.lock.json') -Raw | ConvertFrom-Json
$sourceParent = Join-Path $project 'sources'
New-Item -ItemType Directory -Path $sourceParent -Force | Out-Null
$state = @()
foreach ($repo in @($lock.repositories | Where-Object { $_.id -in @('linux-sm8250', 'kebab-bringup') })) {
    if ($repo.path -notmatch '^sources/[A-Za-z0-9_-]+$' -or $repo.commit -notmatch '^[a-f0-9]{40}$') {
        throw 'Invalid source path or commit in lock.'
    }
    $destination = Join-Path $project $repo.path
    if (-not (Test-Path -LiteralPath $destination)) {
        $arguments = @('-c', 'http.sslBackend=openssl', '-c', 'core.autocrlf=false', 'clone', '--no-checkout')
        if ($repo.id -eq 'linux-sm8250') { $arguments += @('--depth', '1', '--single-branch', '--branch', $repo.ref) }
        $arguments += @($repo.url, $destination)
        $result = Invoke-CapturedProcess -FilePath (Get-Command git -ErrorAction Stop).Source `
            -ArgumentList $arguments -TimeoutSeconds 3600
        if ($result.ExitCode -ne 0) { throw "Source download failed: $($result.Stderr)" }
        if ($repo.id -eq 'kebab-bringup') {
            Invoke-CheckedGit -Repository $destination -GitArguments @('-c', 'core.autocrlf=false',
                'checkout', '--detach', $repo.commit) | Out-Null
        }
    }
    # Never checkout/reset an existing source working tree.
    $actual = Invoke-CheckedGit -Repository $destination -GitArguments @('rev-parse', 'HEAD')
    if ($actual -ne $repo.commit) { throw "Existing source HEAD differs from pin: $($repo.id). Review it without discarding changes." }
    $state += [pscustomobject]@{ Id = $repo.id; Commit = $actual; Directory = $destination; VerifiedAt = [DateTimeOffset]::Now.ToString('o') }
}
$state | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $sourceParent 'source-state.json') -Encoding utf8
$state | Select-Object Id,Commit,Directory
