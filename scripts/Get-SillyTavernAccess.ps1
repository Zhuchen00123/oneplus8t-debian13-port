$ErrorActionPreference='Stop'
$projectRoot=Split-Path $PSScriptRoot -Parent
$taskPython=Join-Path $env:LOCALAPPDATA 'Programs/Python/Python313/python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Python 3.13 with paramiko is required.' }
& $taskPython -X utf8 (Join-Path $PSScriptRoot 'sillytavern/access.py')
if ($LASTEXITCODE -ne 0) { throw 'Dedicated relay is unavailable or still recovering.' }
