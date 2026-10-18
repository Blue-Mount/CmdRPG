param(
    [switch]$New,
    [switch]$Campaign,
    [string]$Name = '远征队',
    [switch]$UploadSave,
    [switch]$DownloadSave,
    [switch]$Replace
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$gamePython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $gamePython)) { $gamePython = 'python' }
if ($UploadSave -and $DownloadSave) { throw '上传与下载不能同时使用。' }
if (($UploadSave -or $DownloadSave) -and ($New -or $Campaign)) { throw '存档同步与开始游戏请分开执行。' }
if ($Replace -and -not ($UploadSave -or $DownloadSave)) { throw '-Replace 仅用于明确覆盖存档。' }
if ($UploadSave -or $DownloadSave) {
    $saveAction = if ($UploadSave) { 'push' } else { 'pull' }
    $saveArguments = @('-m', 'tools.sync_saves', $saveAction)
    if ($Replace) { $saveArguments += '--replace' }
    & $gamePython @saveArguments
}
elseif ($Campaign) { & $gamePython -m client --campaign --name $Name }
elseif ($New) { & $gamePython -m client --new --name $Name }
else { & $gamePython -m client }
exit $LASTEXITCODE
