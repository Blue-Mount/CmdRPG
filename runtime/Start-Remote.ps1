param([string]$Root, [string]$Python, [switch]$New, [string]$Run, [switch]$ChangeToken)
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$previousToken = $env:CMDRPG_TOKEN
try {
    if ($New -and $Run) { throw 'Cannot combine New and Run.' }
    $settings = Get-Content -LiteralPath (Join-Path $Root 'installation.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $config = Join-Path $Root 'data/remote-client.json'
    if (Test-Path -LiteralPath $config) {
        $last = (Get-Content -LiteralPath $config -Raw -Encoding UTF8 | ConvertFrom-Json).last
        if ($last.mode -ne 'remote' -or $last.url -ne ([string]$settings.url).TrimEnd('/') -or !$last.run_id) {
            throw 'Saved remote connection does not match installation settings; refusing implicit new run.'
        }
    }
    # The working directory is the selected immutable release, never the old
    # manual client directory. All mutable client state uses an absolute path.
    Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
    . (Join-Path $PSScriptRoot 'Remote-Token.ps1')
    $tokenPath = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'CmdRPG\token.dpapi'
    $secret = Get-CmdRpgToken -Path $tokenPath -Replace:$ChangeToken
    try {
        $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
        try { $env:CMDRPG_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
        finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
    } finally { $secret.Dispose() }
    $clientArgs = @('-m','client','--url',[string]$settings.url,'--config',$config)
    if ($New) { $clientArgs += '--new' }
    if ($Run) { $clientArgs += @('--run',$Run) }
    if (!$New -and !$Run -and !(Test-Path -LiteralPath $config)) {
        if (!$settings.run_id) { throw 'No saved run. Use New-Run.cmd or --run explicitly.' }
        $clientArgs += @('--run',[string]$settings.run_id)
    }
    & $Python @clientArgs
    exit $LASTEXITCODE
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
} finally { $env:CMDRPG_TOKEN = $previousToken }
