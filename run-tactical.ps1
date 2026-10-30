param(
    [ValidateSet('foundry', 'archive', 'sanctum')]
    [string]$Encounter = 'foundry',
    [int]$Seed = 42,
    [switch]$Baseline,
    [switch]$NoGear,
    [ValidateSet('auto', 'cards', 'plain')]
    [string]$UI = 'auto'
)
$ErrorActionPreference = 'Stop'
$gamePython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $gamePython)) { $gamePython = 'python' }
$sceneLevels = @{ foundry = 3; archive = 4; sanctum = 7 }
$gameArguments = @('-X', 'utf8', '-m', 'tools.dev_play', '--encounter', $Encounter,
    '--level', $sceneLevels[$Encounter], '--seed', $Seed,
    '--classes', 'fighter,rogue,wizard,cleric', '--ui', $UI)
if (-not $Baseline) {
    $gameArguments += '--tactical'
    if (-not $NoGear) { $gameArguments += '--tactical-gear' }
}
Push-Location -LiteralPath $PSScriptRoot
try {
    & $gamePython @gameArguments
    $gameExitCode = $LASTEXITCODE
}
finally { Pop-Location }
exit $gameExitCode
