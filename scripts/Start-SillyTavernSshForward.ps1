param([ValidateRange(1024,65535)][int]$ListenPort=2222)
$ErrorActionPreference='Stop'
$taskPython=Join-Path $env:LOCALAPPDATA 'Programs/Python/Python313/python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Python 3.13 with paramiko is required.' }
& $taskPython -X utf8 (Join-Path $PSScriptRoot 'sillytavern/ssh_forward.py') --listen-port $ListenPort
