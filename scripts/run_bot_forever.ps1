$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'bot_watchdog_policy.ps1')
$projectRoot=Split-Path -Parent $PSScriptRoot
$projectPython=Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) { throw 'Project .venv Python not found.' }
$crashes=@()
Push-Location -LiteralPath $projectRoot
try {
    while ($true) {
        $stopRequest=Join-Path $projectRoot 'runtime\bot.stop'
        if (Test-Path -LiteralPath $stopRequest) { Remove-Item -LiteralPath $stopRequest; exit 0 }
        Write-Host 'Starting bot. Keep this window open. Ctrl+C stops it.'
        & $projectPython -u bot.py
        $result=$LASTEXITCODE
        $now=Get-Date
        Write-Host "Bot exited; code=$result"
        $crashes=@($crashes | Where-Object {$_ -ge $now.AddMinutes(-10)}) + @($now)
        $decision=Get-BotRestartDecision $result $crashes $now
        if ($decision -eq 'stop') { exit $result }
        if ($decision -eq 'error') { Write-Error 'Bot repeatedly crashed. Check logs/bot.log'; exit 1 }
        Start-Sleep -Seconds 10
    }
} finally { Pop-Location }
