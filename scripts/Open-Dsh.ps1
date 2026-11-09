[CmdletBinding()]
param([switch]$Check, [ValidateRange(1,65535)][int]$WebPort = 3080)
$ErrorActionPreference = 'Stop'
$taskPython = Join-Path $env:LOCALAPPDATA 'Programs/Python/Python313/python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Python 3.13 with paramiko is required.' }
$arguments = @('-X', 'utf8', (Join-Path $PSScriptRoot 'dsh/access.py'), '--web-port', "$WebPort")
if ($Check) { $arguments += '--check' }
& $taskPython @arguments
if ($LASTEXITCODE -ne 0) { throw 'DSH access is unavailable; check the relay and service.' }
