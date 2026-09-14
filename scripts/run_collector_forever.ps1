$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'watchdog_policy.ps1')
$projectRoot = Split-Path -Parent $PSScriptRoot
$projectPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) { throw 'Project .venv Python not found.' }
$crashes = @()
Push-Location -LiteralPath $projectRoot
try {
    while ($true) {
        $stopRequest = Join-Path $projectRoot 'runtime\collector.stop'
        if (Test-Path -LiteralPath $stopRequest) {
            Remove-Item -LiteralPath $stopRequest
            Write-Host 'Collector watchdog stopped by explicit request.'
            exit 0
        }
        Write-Host 'Collector watchdog: starting foreground collector. Ctrl+C stops it.'
        & $projectPython -u collector.py
        $result = $LASTEXITCODE
        $now = Get-Date
        $crashes = @($crashes | Where-Object { $_ -ge $now.AddMinutes(-10) }) + @($now)
        $decision = Get-CollectorRestartDecision -ExitCode $result -Crashes $crashes -Now $now
        if ($decision -eq 'stop') { exit $result }
        if ($decision -eq 'error') {
            Write-Error 'Collector stopped after five failures in ten minutes. Inspect logs/collector.log.'
            exit 1
        }
        Write-Host "Collector exited with code $result; retrying in 10 seconds."
        Start-Sleep -Seconds 10
    }
} finally { Pop-Location }
