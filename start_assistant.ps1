$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$localEnvFile = Join-Path $root "assistant.local.env.ps1"
$python = Join-Path $root ".venv\Scripts\python.exe"
$cloudflared = "C:\Program Files (x86)\cloudflared\cloudflared.exe"
$pidFile = Join-Path $root "data\service_state.json"
$stdoutLog = Join-Path $root "cloudflared-temp.out.log"
$stderrLog = Join-Path $root "cloudflared-temp.err.log"

if (Test-Path $localEnvFile) {
    . $localEnvFile
}

if ($env:GOOGLE_CLIENT_SECRET -match "^\s*client\s*secret\s*:") {
    $env:GOOGLE_CLIENT_SECRET = ($env:GOOGLE_CLIENT_SECRET -replace "^\s*client\s*secret\s*:\s*", "").Trim()
}

if (-not (Test-Path $python)) {
    throw "Virtual environment Python not found at $python"
}

if (-not (Test-Path $cloudflared)) {
    throw "cloudflared not found at $cloudflared"
}

if (Test-Path $pidFile) {
    $existingState = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
    foreach ($existingPid in @($existingState.tunnel_pid, $existingState.api_pid)) {
        if ($existingPid) {
            $existingProcess = Get-Process -Id $existingPid -ErrorAction SilentlyContinue
            if ($existingProcess) {
                Stop-Process -Id $existingPid -Force -ErrorAction SilentlyContinue
                Start-Sleep -Milliseconds 500
            }
        }
    }
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
}

if (Test-Path $stdoutLog) {
    try {
        Remove-Item -LiteralPath $stdoutLog -Force
    } catch {
        $stdoutLog = Join-Path $root ("cloudflared-temp-{0}.out.log" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
    }
}

if (Test-Path $stderrLog) {
    try {
        Remove-Item -LiteralPath $stderrLog -Force
    } catch {
        $stderrLog = Join-Path $root ("cloudflared-temp-{0}.err.log" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
    }
}

$apiProcess = Start-Process `
    -FilePath $python `
    -ArgumentList @("-m", "uvicorn", "src.api_server:app", "--host", "127.0.0.1", "--port", "8000") `
    -WorkingDirectory $root `
    -PassThru

Start-Sleep -Seconds 4

try {
    $null = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:8000/"
} catch {
    if ($apiProcess -and -not $apiProcess.HasExited) {
        Stop-Process -Id $apiProcess.Id -Force
    }
    throw "Assistant API did not start correctly."
}

$tunnelProcess = Start-Process `
    -FilePath $cloudflared `
    -ArgumentList @("tunnel", "--url", "http://127.0.0.1:8000", "--no-autoupdate", "--loglevel", "info") `
    -WorkingDirectory $root `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -PassThru

$publicUrl = $null
for ($i = 0; $i -lt 15; $i++) {
    Start-Sleep -Seconds 2
    if (Test-Path $stderrLog) {
        $match = Select-String -Path $stderrLog -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' | Select-Object -First 1
        if ($match) {
            $publicUrl = $match.Matches[0].Value
            break
        }
    }
}

$state = @{
    api_pid = $apiProcess.Id
    tunnel_pid = $tunnelProcess.Id
    public_url = $publicUrl
    started_at = (Get-Date).ToString("o")
} | ConvertTo-Json

Set-Content -LiteralPath $pidFile -Value $state -Encoding UTF8

Write-Host "Assistant API started."
Write-Host "API docs: http://127.0.0.1:8000/docs"
Write-Host "Local OpenAPI: http://127.0.0.1:8000/openapi.json"
Write-Host "Local Connect Page: http://127.0.0.1:8000/connect-google-calendar"
if ($publicUrl) {
    Write-Host "Public URL: $publicUrl"
    Write-Host "OpenAPI: $publicUrl/openapi.json"
    Write-Host "GPT Action URL: $publicUrl/openapi.json"
    Write-Host "User Connect Page: $publicUrl/connect-google-calendar"
} else {
    Write-Host "Tunnel started, but the public URL was not detected yet. Check $stderrLog"
}
Write-Host "State file: $pidFile"
