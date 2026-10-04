#Requires -Version 5.1
<#
.SYNOPSIS
  中继链路端到端联调（不碰手机）。

  拓扑：
    本机测试客户端 → mcbridge forward（好友角色，监听 127.0.0.1:25565）
        → wss://<relay>:443/f/<token>/<rand>/up|down
        → [CNB] mcbridge serve
        → wss://<relay>:443/t/<secret>/<session>/up|down
        → mcbridge client（手机角色）→ 127.0.0.1:25566（假 MC）

  任一段不通都会在本脚本的输出里体现出来。
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
$log = Join-Path $here 'e2e-logs'
New-Item -ItemType Directory -Force -Path $log | Out-Null

function Stop-Quiet($p) {
  if ($p -and -not $p.HasExited) { try { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue } catch {} }
}

Write-Output "===== 0. 环境检查 ====="
if (-not (Test-Path $ClientExe)) { Write-Output "  ❌ 缺少 $ClientExe"; exit 1 }
if (-not (Test-Path $Python)) { Write-Output "  ❌ 缺少 $Python"; exit 1 }
Write-Output "  client exe : $ClientExe"
Write-Output "  python     : $Python"
Write-Output "  relay      : $RelayHost`:$RelayPort"

Write-Output ""
Write-Output "===== 1. 中继 /status 可达性 ====="
try {
  $r = Invoke-WebRequest -Uri "https://$RelayHost/status" -TimeoutSec 15 -UseBasicParsing
  Write-Output "  HTTP $($r.StatusCode)，前几行："
  ($r.Content -split "`n" | Select-Object -First 6) | ForEach-Object { "    $_" }
} catch {
  Write-Output "  ❌ /status 取不到：$($_.Exception.Message)"
}

$fake = $null; $cli = $null; $fwd = $null
try {
  Write-Output ""
  Write-Output "===== 2. 启动假 MC（127.0.0.1:$McPort）====="
  $fake = Start-Process -FilePath $Python `
    -ArgumentList @((Join-Path $here 'fake-mc.py'), '127.0.0.1', "$McPort") `
    -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $log 'fake-mc.out') `
    -RedirectStandardError (Join-Path $log 'fake-mc.err')
  Start-Sleep -Seconds 2
  Write-Output "  pid=$($fake.Id) 存活=$(-not $fake.HasExited)"

  Write-Output ""
  Write-Output "===== 3. 启动 client（手机角色）====="
  $cli = Start-Process -FilePath $ClientExe `
    -ArgumentList @('client', '--host', $RelayHost, '--port', "$RelayPort", '--secret', $TunSecret,
                    '--mc', "127.0.0.1:$McPort", '--session', $Session) `
    -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $log 'client.out') `
    -RedirectStandardError (Join-Path $log 'client.err')
  Start-Sleep -Seconds 6
  Write-Output "  pid=$($cli.Id) 存活=$(-not $cli.HasExited)"

  Write-Output ""
  Write-Output "===== 4. 启动 forward（好友角色，监听 127.0.0.1:$ListenPort）====="
  $fwd = Start-Process -FilePath $ClientExe `
    -ArgumentList @('forward', '--host', $RelayHost, '--port', "$RelayPort", '--token', $FwdToken,
                    '--listen', "127.0.0.1:$ListenPort", '--no-gui') `
    -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $log 'fwd.out') `
    -RedirectStandardError (Join-Path $log 'fwd.err')
  Start-Sleep -Seconds 8
  Write-Output "  pid=$($fwd.Id) 存活=$(-not $fwd.HasExited)"

  Write-Output ""
  Write-Output "===== 5. 穿链路测试（127.0.0.1:$ListenPort → 假 MC）====="
  $ok = $false
  for ($i = 1; $i -le 3; $i++) {
    Write-Output "  --- 第 $i 次尝试 ---"
    try {
      $t = New-Object System.Net.Sockets.TcpClient('127.0.0.1', $ListenPort)
      $t.ReceiveTimeout = 12000
      $s = $t.GetStream()
      $buf = New-Object byte[] 256
      $n = $s.Read($buf, 0, 256)
      $banner = [Text.Encoding]::ASCII.GetString($buf, 0, $n).Trim()
      Write-Output "    收到 banner: '$banner'"
      if ($banner -like 'FAKE-MC-BANNER*') {
        $msg = [Text.Encoding]::ASCII.GetBytes("hello-relay`n")
        $s.Write($msg, 0, $msg.Length); $s.Flush()
        Start-Sleep -Milliseconds 900
        $n2 = $s.Read($buf, 0, 256)
        $echo = [Text.Encoding]::ASCII.GetString($buf, 0, $n2).Trim()
        Write-Output "    收到回显  : '$echo'"
        if ($echo -like 'ECHO:*hello-relay*') { $ok = $true }
      }
      $t.Close()
    } catch {
      Write-Output "    失败: $($_.Exception.Message)"
    }
    if ($ok) { break }
    Start-Sleep -Seconds 4
  }

  Write-Output ""
  Write-Output "===== 6. 链路状态 ====="
  try {
    $r2 = Invoke-WebRequest -Uri "https://$RelayHost/status" -TimeoutSec 15 -UseBasicParsing
    ($r2.Content -split "`n" | Select-Object -First 12) | ForEach-Object { "    $_" }
  } catch { Write-Output "  /status 取不到" }

  Write-Output ""
  Write-Output "===== 7. 日志 ====="
  foreach ($f in @('fake-mc.out', 'client.out', 'client.err', 'fwd.out', 'fwd.err')) {
    $p = Join-Path $log $f
    Write-Output "  --- $f ---"
    if (Test-Path $p) { (Get-Content $p -ErrorAction SilentlyContinue | Select-Object -Last 12) | ForEach-Object { "    $_" } }
    else { Write-Output "    (无)" }
  }

  Write-Output ""
  if ($ok) { Write-Output "===== 结论：链路通 ✅ 端到端回显成功 =====" }
  else { Write-Output "===== 结论：链路不通 ❌ =====" }
}
finally {
  Write-Output ""
  Write-Output "===== 清理 ====="
  Stop-Quiet $fwd; Stop-Quiet $cli; Stop-Quiet $fake
  Start-Sleep -Seconds 1
  Write-Output "  已停止全部子进程"
}
