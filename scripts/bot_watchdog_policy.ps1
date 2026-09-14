function Get-BotRestartDecision {
    param([int]$ExitCode,[datetime[]]$Crashes,[datetime]$Now)
    if ($ExitCode -in @(0,3,4,130,-1073741510)) { return 'stop' }
    if (@($Crashes | Where-Object {$_ -ge $Now.AddMinutes(-10)}).Count -ge 5) { return 'error' }
    return 'retry'
}
