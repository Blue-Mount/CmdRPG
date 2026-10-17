param([string]$Database, [string]$ClientReleases)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$databasePath = if ($Database) {
    # Validate before loading credentials. A typo must not start an empty game.
    (Resolve-Path -LiteralPath $Database -ErrorAction Stop).Path
} else { Join-Path $projectRoot 'data\game.sqlite3' }
$releasePath = if ($ClientReleases) {
    (Resolve-Path -LiteralPath $ClientReleases -ErrorAction Stop).Path
} else { $null }
$secretDirectory = Join-Path $env:LOCALAPPDATA 'CmdRPG'
$secretPath = Join-Path $secretDirectory 'server-token.dpapi'
if (-not (Test-Path -LiteralPath $secretPath)) {
    New-Item -ItemType Directory -Path $secretDirectory -Force | Out-Null
    $randomBytes = New-Object byte[] 32
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($randomBytes) } finally { $generator.Dispose() }
    $newToken = [Convert]::ToBase64String($randomBytes)
    $newToken | ConvertTo-SecureString -AsPlainText -Force | ConvertFrom-SecureString | Set-Content -LiteralPath $secretPath
    $newToken = $null
}
$secureToken = (Get-Content -LiteralPath $secretPath -Raw).Trim() | ConvertTo-SecureString
$credential = New-Object System.Net.NetworkCredential('', $secureToken)
$env:CMDRPG_TOKEN = $credential.Password
$env:CMDRPG_DB = $databasePath
if ($releasePath) { $env:CMDRPG_CLIENT_RELEASES = $releasePath }
try {
    & (Join-Path $projectRoot '.venv\Scripts\python.exe') -m uvicorn server.app:app --host 127.0.0.1 --port 8765 --workers 1
    exit $LASTEXITCODE
} finally {
    Remove-Item Env:CMDRPG_TOKEN -ErrorAction SilentlyContinue
}
