$ErrorActionPreference='Stop'
$projectRoot=Split-Path -Parent $PSScriptRoot
$projectPython=Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) { throw 'Project .venv Python not found.' }
$probe=@"
from pathlib import Path
from services.collector_runtime import InstanceLock, AlreadyRunning
import sys
try:
    with InstanceLock(Path('runtime')/(sys.argv[1]+'.lock')): pass
except AlreadyRunning: sys.exit(3)
"@
Push-Location -LiteralPath $projectRoot
try {
    foreach ($entry in @('bot','collector')) {
        & $projectPython -c $probe $entry
        if ($LASTEXITCODE -eq 3) { Write-Host "$entry is already running."; continue }
        if ($LASTEXITCODE -ne 0) { throw 'Instance check failed.' }
        $script=Join-Path $PSScriptRoot "run_${entry}_forever.ps1"
        # Separate visible consoles are intentional for this interactive launcher.
        Start-Process powershell.exe -WindowStyle Normal -WorkingDirectory $projectRoot -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File', ('"'+$script+'"')) | Out-Null
    }
} finally { Pop-Location }
