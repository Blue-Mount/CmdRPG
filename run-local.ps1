param([switch]$New, [string]$Name = '远征队')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$gamePython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $gamePython)) { $gamePython = 'python' }
if ($New) { & $gamePython -m client --new --name $Name }
else { & $gamePython -m client }
exit $LASTEXITCODE
