$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$projectPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) { throw 'Project .venv Python not found.' }
Push-Location -LiteralPath $projectRoot
try {
    Write-Host 'Starting Dubai Job Assistant bot in foreground. Ctrl+C stops it.'
    & $projectPython -u bot.py
    exit $LASTEXITCODE
} finally { Pop-Location }
