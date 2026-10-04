#Requires -Version 5.1
<#
  Full-chain relay test (no phone involved).

    test client --> mcbridge.exe forward (friend role, 127.0.0.1:25565)
                 --> wss://<relay>:443/f/<token>/<rand>/up|down
                 --> [CNB] mcbridge serve
                 --> wss://<relay>:443/t/<secret>/<session>/up|down
                 --> mcbridge_client.py (phone role)
                 --> 127.0.0.1:25566 (fake MC)

  ASCII-only on purpose: Windows PowerShell 5.1 reads BOM-less UTF-8 as ANSI.
#>
param(
  [string]$RelayHost = 'zzsh2enrz7-9000.cnb.run',
  [int]$RelayPort = 443,
  [string]$TunSecret = 'wst_9f3a7c2e5b18d460',
  [string]$FwdToken = 'fwd_2b7e91c4d6a80f53',
  [string]$Session = 'kebab',
  [string]$ClientExe = 'F:\codexprojects\mcfuwuqi\friend-package\mcbridge.exe',
  [string]$Python = 'C:\Miniconda\python.exe',
  [int]$McPort = 25566,
  [int]$ListenPort = 25565
)

$ErrorActionPreference = 'Continue'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$mcDir = Split-Path -Parent $here
$clientPy = Join-Path $mcDir 'client\mcbridge_client.py'
$fakePy = Join-Path $here 'fake-mc.py'
$logDir = Join-Path $here 'e2e-logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Stop-Quiet($p) {
  if ($p) { try { if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue } } catch {} }
}

Write-Output "===== 0. preflight ====="
foreach ($f in @($ClientExe, $Python, $clientPy, $fakePy)) {
  if (Test-Path $f) { Write-Output "  OK   $f" } else { Write-Output "  MISS $f"; exit 1 }
}

$fake = $null; $cli = $null; $fwd = $null
try {
  Write-Output ""
  Write-Output "===== 1. fake MC on 127.0.0.1:$McPort ====="
  $fake = Start-Process -FilePath $Python -ArgumentList @($fakePy, '127.0.0.1', "$McPort") `
    -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logDir 'fake.out') `
    -RedirectStandardError (Join-Path $logDir 'fake.err')
  Start-Sleep -Seconds 2
  Write-Output "  pid=$($fake.Id) alive=$(-not $fake.HasExited)"

  Write-Output ""
  Write-Output "===== 2. phone-role client (python) ====="
  $env:PYTHONIOENCODING = 'utf-8'
  $cli = Start-Process -FilePath $Python `
    -ArgumentList @($clientPy, '--host', $RelayHost, '--port', "$RelayPort",
                    '--secret', $TunSecret, '--session', $Session,
                    '--mc', "127.0.0.1:$McPort", '--debug') `
    -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logDir 'cli.out') `
    -RedirectStandardError (Join-Path $logDir 'cli.err')
  Start-Sleep -Seconds 6
  Write-Output "  pid=$($cli.Id) alive=$(-not $cli.HasExited)"
  Write-Output "  --- client log ---"
  if (Test-Path (Join-Path $logDir 'cli.out')) {
    Get-Content (Join-Path $logDir 'cli.out') | Select-Object -Last 8 | ForEach-Object { "    $_" }
  }
  if (Test-Path (Join-Path $logDir 'cli.err')) {
    Get-Content (Join-Path $logDir 'cli.err') | Select-Object -Last 8 | ForEach-Object { "    ERR $_" }
  }

  Write-Output ""
  Write-Output "===== 3. friend-role forward (real exe) on 127.0.0.1:$ListenPort ====="
  $fwd = Start-Process -FilePath $ClientExe `
    -ArgumentList @('forward', '--host', $RelayHost, '--port', "$RelayPort",
                    '--token', $FwdToken, '--listen', "127.0.0.1:$ListenPort", '--no-gui') `
    -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logDir 'fwd.out') `
    -RedirectStandardError (Join-Path $logDir 'fwd.err')
  Start-Sleep -Seconds 8
  Write-Output "  pid=$($fwd.Id) alive=$(-not $fwd.HasExited)"
  Write-Output "  --- forward log ---"
  if (Test-Path (Join-Path $logDir 'fwd.out')) {
    Get-Content (Join-Path $logDir 'fwd.out') | Select-Object -Last 8 | ForEach-Object { "    $_" }
  }
  if (Test-Path (Join-Path $logDir 'fwd.err')) {
    Get-Content (Join-Path $logDir 'fwd.err') | Select-Object -Last 8 | ForEach-Object { "    ERR $_" }
  }

  Write-Output ""
  Write-Output "===== 4. end-to-end probe ====="
  $ok = $false
  for ($i = 1; $i -le 3; $i++) {
    Write-Output "  --- attempt $i ---"
    try {
      $t = New-Object System.Net.Sockets.TcpClient('127.0.0.1', $ListenPort)
      $t.ReceiveTimeout = 15000
      $s = $t.GetStream()
      $buf = New-Object byte[] 256
      $n = $s.Read($buf, 0, 256)
      $banner = [Text.Encoding]::ASCII.GetString($buf, 0, $n).Trim()
      Write-Output "    banner: '$banner'"
      if ($banner -like 'FAKE-MC-BANNER*') {
        $msg = [Text.Encoding]::ASCII.GetBytes("hello-through-relay`n")
        $s.Write($msg, 0, $msg.Length); $s.Flush()
        Start-Sleep -Milliseconds 900
        $n2 = $s.Read($buf, 0, 256)
        $echo = [Text.Encoding]::ASCII.GetString($buf, 0, $n2).Trim()
        Write-Output "    echo  : '$echo'"
        if ($echo -like 'ECHO:*hello-through-relay*') { $ok = $true }
      }
      $t.Close()
    } catch {
      Write-Output "    FAIL: $($_.Exception.Message)"
    }
    if ($ok) { break }
    Start-Sleep -Seconds 4
  }

  Write-Output ""
  Write-Output "===== 5. relay /status ====="
  try {
    $r = Invoke-WebRequest -Uri "https://$RelayHost/status" -TimeoutSec 15 -UseBasicParsing
    ($r.Content -split "`n" | Select-Object -First 12) | ForEach-Object { "    $_" }
  } catch { Write-Output "    status failed: $($_.Exception.Message)" }

  Write-Output ""
  Write-Output "===== 6. client log after test ====="
  if (Test-Path (Join-Path $logDir 'cli.out')) {
    Get-Content (Join-Path $logDir 'cli.out') | Select-Object -Last 25 | ForEach-Object { "    $_" }
  }

  Write-Output ""
  if ($ok) { Write-Output "===== RESULT: FULL CHAIN OK =====" }
  else { Write-Output "===== RESULT: CHAIN FAILED =====" }
}
finally {
  Write-Output ""
  Write-Output "===== cleanup ====="
  Stop-Quiet $fwd; Stop-Quiet $cli; Stop-Quiet $fake
  Start-Sleep -Seconds 1
  Write-Output "  stopped"
}
