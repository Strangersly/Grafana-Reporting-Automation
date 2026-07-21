[CmdletBinding()]
param(
    [string]$WslDistribution = "Ubuntu"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$statePath = Join-Path $projectRoot ".runtime\processes.json"

function Stop-ProcessTree {
    param([int]$ProcessId)

    $children = @(Get-CimInstance Win32_Process | Where-Object { $_.ParentProcessId -eq $ProcessId })
    foreach ($child in $children) {
        Stop-ProcessTree -ProcessId ([int]$child.ProcessId)
    }
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

function Stop-TrackedProcess {
    param(
        [int]$ProcessId,
        [string]$ExpectedPattern
    )

    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    if ($process -and $process.CommandLine -match $ExpectedPattern) {
        Stop-ProcessTree -ProcessId $ProcessId
    }
}

if (-not (Test-Path -LiteralPath $statePath)) {
    Write-Host "No tracked local services were found."
    exit 0
}

$state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
Stop-TrackedProcess -ProcessId ([int]$state.worker) -ExpectedPattern "celery.*grafana_web.*worker"
Stop-TrackedProcess -ProcessId ([int]$state.beat) -ExpectedPattern "celery.*grafana_web.*beat"
Stop-TrackedProcess -ProcessId ([int]$state.web) -ExpectedPattern "manage\.py\s+runserver"

try {
    & wsl.exe -d $WslDistribution -u root -- systemctl stop redis-server
}
catch {
    Write-Warning "Redis could not be stopped cleanly: $($_.Exception.Message)"
}
Stop-TrackedProcess -ProcessId ([int]$state.redisWsl) -ExpectedPattern "wsl.*sleep\s+infinity"

Remove-Item -LiteralPath $statePath -Force -ErrorAction SilentlyContinue
Write-Host "Grafana Reporting local services stopped."
