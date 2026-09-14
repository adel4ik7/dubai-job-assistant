function Get-CollectorRestartDecision {
    param([int]$ExitCode, [datetime[]]$Crashes, [datetime]$Now)
    # Normal stop, Ctrl+C and another running instance must never trigger restart.
    if ($ExitCode -in @(0, 3, 130, -1073741510)) { return 'stop' }
    $recent = @($Crashes | Where-Object { $_ -ge $Now.AddMinutes(-10) })
    if ($recent.Count -ge 5) { return 'error' }
    return 'retry'
}
