function Get-CmdRpgToken {
    param([string]$Path, [switch]$Replace)
    # Missing DPAPI cmdlets must never be mistaken for a corrupt credential.
    Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1') -ErrorAction Stop
    if (!$Replace -and (Test-Path -LiteralPath $Path)) {
        $cached = $null
        try {
            $encrypted = [IO.File]::ReadAllText($Path)
            $cached = ConvertTo-SecureString -String $encrypted -ErrorAction Stop
            if ($cached.Length -eq 0) { throw 'Empty credential' }
            return $cached
        } catch {
            if ($cached) { $cached.Dispose() }
            Write-Host '保存的 token 无法解密，请重新输入。' -ForegroundColor Yellow
        }
    }
    $secret = Read-Host '请输入服务器 CMDRPG_TOKEN（隐藏输入，将为当前 Windows 用户加密保存）' -AsSecureString
    $temporary = $null
    try {
        if ($secret.Length -eq 0) { throw 'token 不能为空。' }
        $directory = Split-Path -Parent $Path
        [IO.Directory]::CreateDirectory($directory) | Out-Null
        # No -Key or -SecureKey: Windows DPAPI binds this to the current user.
        $encrypted = ConvertFrom-SecureString -SecureString $secret -ErrorAction Stop
        $temporary = Join-Path $directory ([IO.Path]::GetRandomFileName())
        [IO.File]::WriteAllText($temporary, $encrypted, (New-Object Text.UTF8Encoding($false)))
        if ([IO.File]::Exists($Path)) {
            Move-Item -LiteralPath $temporary -Destination $Path -Force -ErrorAction Stop
        } else {
            [IO.File]::Move($temporary, $Path)
        }
        return $secret
    } catch {
        $secret.Dispose()
        throw '无法保存加密 token；请检查当前用户 LocalAppData 目录权限。'
    } finally {
        if ($temporary -and [IO.File]::Exists($temporary)) { [IO.File]::Delete($temporary) }
    }
}
