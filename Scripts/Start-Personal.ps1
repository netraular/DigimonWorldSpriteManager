$ErrorActionPreference = "Stop"

$info = New-Object System.Diagnostics.ProcessStartInfo
$info.FileName = Join-Path $PSScriptRoot "main.exe"
$info.WorkingDirectory = $PSScriptRoot
$info.UseShellExecute = $false
$info.RedirectStandardInput = $true
$info.RedirectStandardOutput = $true

$process = [System.Diagnostics.Process]::Start($info)
$acknowledged = $false
$finished = $false
$timer = [System.Diagnostics.Stopwatch]::StartNew()
try {
    $process.StandardInput.WriteLine("online autosync off")
    $process.StandardInput.WriteLine("online autosync")
    $process.StandardInput.Flush()
    $read = $process.StandardOutput.ReadLineAsync()
    while ($true) {
        if ($read.Wait(100)) {
            $line = $read.Result
            if ($null -eq $line) {
                break
            }
            [Console]::WriteLine($line)
            if ($line.Trim() -eq "auto content install: off") {
                $acknowledged = $true
            }
            $read = $process.StandardOutput.ReadLineAsync()
        }
        if (-not $acknowledged -and $timer.Elapsed.TotalSeconds -ge 45) {
            throw "Could not disable automatic content installation; stopping to protect the private catalogue."
        }
    }
    if (-not $acknowledged) {
        throw "HibiTomo exited without confirming that automatic content installation is off."
    }
    $process.WaitForExit()
    $exitCode = $process.ExitCode
    $finished = $true
} finally {
    if (-not $finished -and -not $process.HasExited) {
        $process.Kill()
        $process.WaitForExit()
    }
    $process.StandardInput.Close()
    $process.Dispose()
}
exit $exitCode
