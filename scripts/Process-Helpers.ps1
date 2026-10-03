#Requires -Version 7.2
Set-StrictMode -Version Latest

function Invoke-CapturedProcess {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [string[]]$ArgumentList = @(),
        [string]$BinaryDestination,
        [ValidateRange(1, 7200)][int]$TimeoutSeconds = 30
    )
    $info = [System.Diagnostics.ProcessStartInfo]::new()
    $info.FileName = $FilePath
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    foreach ($item in $ArgumentList) { $info.ArgumentList.Add($item) }
    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $info
    $fileStream = $null
    $started = $false
    try {
        $started = $process.Start()
        if (-not $started) { throw "Cannot start $FilePath" }
        $errorTask = $process.StandardError.ReadToEndAsync()
        if ($BinaryDestination) {
            # Never pass binary output through PowerShell's text pipeline.
            $fileStream = [System.IO.File]::Open($BinaryDestination,
                [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write,
                [System.IO.FileShare]::None)
            $outputTask = $process.StandardOutput.BaseStream.CopyToAsync($fileStream)
        } else {
            $outputTask = $process.StandardOutput.ReadToEndAsync()
        }
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            $process.Kill($true)
            $process.WaitForExit()
            try { $outputTask.GetAwaiter().GetResult() | Out-Null } catch {}
            throw "Process timed out after $TimeoutSeconds seconds: $FilePath"
        }
        if ($BinaryDestination) {
            $outputTask.GetAwaiter().GetResult() | Out-Null
            $fileStream.Flush($true)
            $output = ''
        } else {
            $output = $outputTask.GetAwaiter().GetResult()
        }
        [pscustomobject]@{
            ExitCode = $process.ExitCode
            Stdout = $output
            Stderr = $errorTask.GetAwaiter().GetResult()
        }
    } finally {
        if ($started -and -not $process.HasExited) {
            $process.Kill($true)
            $process.WaitForExit()
        }
        if ($null -ne $fileStream) { $fileStream.Dispose() }
        $process.Dispose()
    }
}

function Invoke-CheckedGit {
    param([string]$Repository, [string[]]$GitArguments, [int]$TimeoutSeconds = 60)
    $result = Invoke-CapturedProcess -FilePath (Get-Command git -ErrorAction Stop).Source `
        -ArgumentList (@('-C', $Repository) + $GitArguments) -TimeoutSeconds $TimeoutSeconds
    if ($result.ExitCode -ne 0) { throw "Git failed: $($result.Stderr)" }
    $result.Stdout.TrimEnd("`r", "`n")
}

function Invoke-AdbRead {
    param([string]$Serial, [string]$Command, [int]$TimeoutSeconds = 30)
    Invoke-CapturedProcess -FilePath (Get-Command adb -ErrorAction Stop).Source `
        -ArgumentList @('-s', $Serial, 'shell', $Command) -TimeoutSeconds $TimeoutSeconds
}

function Assert-AdbDevice {
    param([string]$Serial, [string]$ExpectedModel)
    $state = Invoke-CapturedProcess -FilePath (Get-Command adb -ErrorAction Stop).Source `
        -ArgumentList @('-s', $Serial, 'get-state')
    if ($state.ExitCode -ne 0 -or $state.Stdout.Trim() -ne 'device') {
        throw "ADB device is unavailable or unauthorized: $($state.Stderr.Trim())"
    }
    $model = Invoke-AdbRead -Serial $Serial -Command 'getprop ro.product.model'
    if ($model.ExitCode -ne 0 -or $model.Stdout.Trim() -ne $ExpectedModel) {
        throw "Device model mismatch: expected $ExpectedModel; read '$($model.Stdout.Trim())'."
    }
}
