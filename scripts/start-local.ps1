[CmdletBinding()]
param(
    [string]$Bind = "127.0.0.1:8000",
    [string]$WslDistribution = "Ubuntu"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimeRoot = Join-Path $projectRoot ".runtime"
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$celery = Join-Path $projectRoot ".venv\Scripts\celery.exe"
$healthUrl = "http://127.0.0.1:8000/health/"
$ignoredProxyVariables = @{}

function Remove-InvalidLoopbackProxy {
    # Codex and some terminal tools inject this unavailable local proxy. Do not pass it to workers.
    foreach ($name in @("ALL_PROXY", "HTTP_PROXY", "HTTPS_PROXY")) {
        $value = [Environment]::GetEnvironmentVariable($name, "Process")
        if ($value -match "^https?://(127\.0\.0\.1|localhost):9/?$") {
            $script:ignoredProxyVariables[$name] = $value
            [Environment]::SetEnvironmentVariable($name, $null, "Process")
        }
    }
}

function Restore-InvalidLoopbackProxy {
    foreach ($entry in $script:ignoredProxyVariables.GetEnumerator()) {
        [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, "Process")
    }
}

function Find-AppProcess {
    param(
        [string]$Pattern,
        [string[]]$Names
    )

    Get-CimInstance Win32_Process |
        Where-Object { $_.Name -in $Names -and $_.CommandLine -match $Pattern } |
        Select-Object -First 1
}

function Get-AppProcessId {
    param([object]$Process)

    if ($Process.PSObject.Properties.Name -contains "ProcessId") {
        return [int]$Process.ProcessId
    }
    return [int]$Process.Id
}

function Test-TcpPort {
    param(
        [string]$HostName,
        [int]$Port
    )

    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $connection = $client.ConnectAsync($HostName, $Port)
        return $connection.Wait(1000) -and $client.Connected
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python environment not found. Run the local setup steps in README.md first."
}
if (-not (Test-Path -LiteralPath $celery)) {
    throw "Celery is not installed in .venv. Run pip install -r requirements.txt."
}
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot ".env"))) {
    throw ".env is missing. Copy .env.example to .env and configure it first."
}

New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
Push-Location $projectRoot
try {
    Remove-InvalidLoopbackProxy
    $keepalive = Find-AppProcess -Pattern "wsl.*sleep\s+infinity" -Names @("wsl.exe")
    if (-not $keepalive) {
        $keepalive = Start-Process -FilePath "wsl.exe" `
            -ArgumentList @("-d", $WslDistribution, "-u", "root", "--", "sleep", "infinity") `
            -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $runtimeRoot "wsl-keepalive.out.log") `
            -RedirectStandardError (Join-Path $runtimeRoot "wsl-keepalive.err.log") `
            -PassThru
        Start-Sleep -Seconds 2
    }

    & wsl.exe -d $WslDistribution -u root -- sh -lc "command -v redis-server >/dev/null"
    if ($LASTEXITCODE -ne 0) {
        throw "Redis is not installed in WSL distribution '$WslDistribution'. See README.md."
    }
    & wsl.exe -d $WslDistribution -u root -- systemctl start redis-server
    if ($LASTEXITCODE -ne 0) {
        throw "Redis could not be started in WSL distribution '$WslDistribution'."
    }

    $redisReady = $false
    for ($attempt = 0; $attempt -lt 15; $attempt++) {
        if (Test-TcpPort -HostName "127.0.0.1" -Port 6379) {
            $redisReady = $true
            break
        }
        Start-Sleep -Seconds 1
    }
    if (-not $redisReady) {
        throw "Redis did not become reachable at 127.0.0.1:6379."
    }

    $worker = Find-AppProcess -Pattern "celery.*grafana_web.*worker" -Names @("celery.exe")
    if (-not $worker) {
        Remove-Item -LiteralPath (Join-Path $runtimeRoot "worker.out.log"), (Join-Path $runtimeRoot "worker.err.log") -Force -ErrorAction SilentlyContinue
        $worker = Start-Process -FilePath $celery `
            -ArgumentList @("-A", "grafana_web", "worker", "--loglevel=INFO", "--pool=solo", "--concurrency=1") `
            -WorkingDirectory $projectRoot `
            -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $runtimeRoot "worker.out.log") `
            -RedirectStandardError (Join-Path $runtimeRoot "worker.err.log") `
            -PassThru
    }

    $beat = Find-AppProcess -Pattern "celery.*grafana_web.*beat" -Names @("celery.exe")
    if (-not $beat) {
        Remove-Item -LiteralPath (Join-Path $runtimeRoot "beat.out.log"), (Join-Path $runtimeRoot "beat.err.log") -Force -ErrorAction SilentlyContinue
        $beat = Start-Process -FilePath $celery `
            -ArgumentList @("-A", "grafana_web", "beat", "--loglevel=INFO", "--schedule=.runtime/celerybeat-schedule") `
            -WorkingDirectory $projectRoot `
            -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $runtimeRoot "beat.out.log") `
            -RedirectStandardError (Join-Path $runtimeRoot "beat.err.log") `
            -PassThru
    }

    $web = Find-AppProcess -Pattern "manage\.py\s+runserver" -Names @("python.exe")
    if (-not $web) {
        Remove-Item -LiteralPath (Join-Path $runtimeRoot "web.out.log"), (Join-Path $runtimeRoot "web.err.log") -Force -ErrorAction SilentlyContinue
        $web = Start-Process -FilePath $python `
            -ArgumentList @("manage.py", "runserver", $Bind, "--noreload") `
            -WorkingDirectory $projectRoot `
            -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $runtimeRoot "web.out.log") `
            -RedirectStandardError (Join-Path $runtimeRoot "web.err.log") `
            -PassThru
    }

    [ordered]@{
        web = Get-AppProcessId -Process $web
        worker = Get-AppProcessId -Process $worker
        beat = Get-AppProcessId -Process $beat
        redisWsl = Get-AppProcessId -Process $keepalive
    } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $runtimeRoot "processes.json")

    $healthy = $false
    for ($attempt = 0; $attempt -lt 45; $attempt++) {
        try {
            $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2
            if ($health.status -eq "ok") {
                $healthy = $true
                break
            }
        }
        catch {
            # Services can take a few seconds to connect and publish a heartbeat.
        }
        Start-Sleep -Seconds 1
    }

    if ($healthy) {
        Write-Host "Grafana Reporting is running: http://127.0.0.1:8000"
        Write-Host "Health check passed. Runtime logs: $runtimeRoot"
    }
    else {
        Write-Warning "Services started, but the health check is still warming up. Inspect $runtimeRoot."
    }
}
finally {
    Restore-InvalidLoopbackProxy
    Pop-Location
}
