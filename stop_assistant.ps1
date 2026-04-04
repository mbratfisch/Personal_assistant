$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$pidFile = Join-Path $root "data\service_state.json"

if (-not (Test-Path $pidFile)) {
    Write-Host "No service state file found. Nothing to stop."
    exit 0
}

$state = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json

foreach ($existingPid in @($state.tunnel_pid, $state.api_pid)) {
    if ($existingPid) {
        $process = Get-Process -Id $existingPid -ErrorAction SilentlyContinue
        if ($process) {
            Stop-Process -Id $existingPid -Force
            Write-Host "Stopped process $existingPid ($($process.ProcessName))."
        }
    }
}

Remove-Item -LiteralPath $pidFile -Force
Write-Host "Assistant services stopped."
