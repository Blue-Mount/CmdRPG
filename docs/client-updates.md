# 客户端发布、安装和自动更新

开发仓库、发布产物与玩家目录必须分开，例如 `D:\CmdRPG-dev`、`D:\CmdRPG-releases`、`D:\CmdRPG-player`。更新器拒绝写入任何带 `.git` 的目录及其子目录。原来的 `D:\CmdRPG` 手动客户端完整保留；迁移后请使用新入口，避免两个安装同时操作同一个周目。

## 一次性迁移

开发者或管理员从经过测试的提交生成发布产物：

```powershell
Set-Location D:\CmdRPG-dev
.\.venv\Scripts\python.exe -m tools.release_client --sequence 1 --output D:\CmdRPG-releases
.\.venv\Scripts\python.exe -m tools.install_client --publication D:\CmdRPG-releases --legacy D:\CmdRPG --destination D:\CmdRPG-player
```

目标目录必须不存在。先关闭旧客户端。迁移只复制 `data/remote-client.json` 和 `data/remote-client.pending.json` 的原始字节，优先采用其 URL、周目 ID；其次兼容旧 `remote-launcher.json` 的 `Url`。两个连接地址不一致时拒绝隐式迁移。全新安装使用 `--url https://node.taildf6f41.ts.net --run-id 1208f8dc-9abd-4244-85ba-1132a21adecd`，已有记录优先。可以用 `--python <Python3.11+的绝对路径>` 指定长期保留的解释器；不指定时使用安装工具当前的解释器。

普通玩家从可信 HTTPS 的 `/client-updates/installer.zip` 下载并解压一次性安装包，然后执行：

```powershell
.\Install-Client.cmd D:\CmdRPG D:\CmdRPG-player
```

需要安装 Python 3.11+（批处理使用 `py -3`；没有 py 时按包内 INSTALL.txt 使用 python.exe）。安装包仅含明确列出的远程客户端、资源和标准库安装工具，不要求 Git、FastAPI 或 GitHub 账户。不要将私有 GitHub 凭据分发给玩家。安装程序不会执行或自动移植任意自定义旧脚本；已核对的旧入口参数和 DPAPI 凭据位置已纳入新版启动逻辑。

## 日常使用和凭据

双击 `D:\CmdRPG-player\Play.cmd`：检查更新后恢复原周目。`New-Run.cmd` 明确创建新周目；`Change-Token.cmd` 隐藏输入更换凭据；`Check-Update.cmd` 只检查更新。也可以执行 `python D:\CmdRPG-player\bootstrap.py --run <ID>`。

凭据复用当前 Windows 账户 `%LOCALAPPDATA%\CmdRPG\token.dpapi`，由 Windows DPAPI 加密。仅缓存不存在或无法解密时隐藏输入，不把明文放进命令参数、JSON、日志或发布包。原连接设置、周目 ID、未确认请求位于玩家根目录 `data/`；版本目录从不包含这些数据。若保留了未确认请求，客户端沿用原 scope、command ID 恢复回执。

启动时合并保留 `NO_PROXY` / `no_proxy`，追加游戏及发布主机。远程只接受 HTTPS，使用正常 CA 证书校验，拒绝重定向；HTTP 仅允许本机回环测试。Tailscale 必须登录同一网络。

## 版本和故障行为

发布入口 `channel.json` 包含递增整数版本、最低版本、完整文件列表和每个文件的 SHA-256/大小。其规范 JSON 的 SHA-256 是不可变发布目录名。客户端通过配置中固定的 HTTPS 发布入口获取可信清单，再从同一入口下载明确列出的文件，不读取 GitHub。

每次更新在 `versions/.staging-*` 完整下载、校验文件、编译 Python 并检查远程客户端/启动器能否加载，最后通过 `os.replace` 原子切换 `current.json`。当前版本文件不会原地覆盖。旧文件只存在于旧版本目录，新的进程仅从新版本目录启动，因此已移除资源不会继续使用。同序号不同内容、版本回退、目录穿越、Windows 特殊文件、链接、清单外文件、超大内容均拒绝。

下载中断、损坏、加载失败或指针提交失败时明确打印原因，继续使用已验证的旧版本。已获取的最低版本高于旧版本时禁止连接；即使更新入口网络故障，游戏 API 也独立以 HTTP 426 拒绝低版本，显示必须升级。不能伪造版本头来获得兼容性，版本号是兼容性门槛而非身份认证。

OS 文件锁覆盖更新和整个游戏子进程生命周期。同一玩家安装只允许一个启动进程；第二个窗口明确退出，进程崩溃后由 OS 释放锁。更新期间不会替换运行中的版本。不同玩家目录应使用独立配置；不要绕过入口同时操作同一配置。

根目录 `bootstrap.py` 和批处理是固定的 ABI-1 转发入口，只定位当前目录并启动其中的管理器；所有会变化的启动、DPAPI、更新策略都在版本化 `runtime/` 中，随发布同步。解释器本身和这个固定 ABI 的迁移属于重新安装范畴。上一份版本保留供诊断，暂不自动清理旧目录/崩溃遗留的暂存目录；不会因清理失败损失回退副本。

## 开发提交如何成为发布

Git 推送和 PR 不会发布到原电脑，也不会重启游戏服务。审阅并合并后，在已验证提交上创建递增标签，例如 `client-v1`：

```powershell
git tag client-v1 <已验证的提交SHA>
git push origin client-v1
```

GitHub Actions 在 Windows / Python 3.11 跑全部测试，成功后按标签序号构建显式文件清单，生成带标签及提交 SHA 名称的 artifact，其中有 `channel.json`、不可变 `releases/` 和 `installer.zip`。新增资源必须加入 `release/client-files.json`，运行时允许的客户端资源类型为 JSON/TXT/PNG；当前远程客户端不需要 core/content/server。

管理员审核 artifact 后，在原电脑执行下面的一次性服务部署，并将客户端产物放到独立发布目录。也可在经过验证的干净 checkout 上运行 `tools.release_client` 直接构建。后续只更新客户端时，在原电脑的独立发布 checkout 拉取已验证标签，运行：

```powershell
python -m tools.release_client --sequence 2 --output D:\CmdRPG-releases
```

发布器锁定发布目录，先准备完整不可变目录和安装包，最后原子提交频道。版本必须严格递增；不要覆盖已发布版本。若确需退回实现，以更大的序号重新发布旧代码。`--minimum 2` 可同时要求玩家至少版本 2；未指定时最低为 0。提高最低版本前验证旧存档、未确认请求及服务端接口兼容性。可选环境变量 `CMDRPG_MIN_CLIENT_VERSION` 用于更严格的服务器门槛。

Actions 只构建 artifact，不自动接触 Tailscale、服务器 token 或生产存档。玩家下次正常打开时完成同步；游戏过程中不后台切换文件。

## 原电脑：一次性部署已验证的新服务代码

以下是操作员在原电脑执行的步骤，本次没有执行生产部署。保留现有 Tailscale Serve 配置（HTTPS 转发到回环 8765）。将 `$oldServer` 改为**原电脑实际服务仓库路径**，并确认数据库位置；示例使用 `D:\CmdRPG`。不要把本机玩家目录当成原电脑服务仓库。

```powershell
$oldServer = (Resolve-Path 'D:\CmdRPG').Path
$database = (Resolve-Path (Join-Path $oldServer 'data\game.sqlite3')).Path
$nextServer = 'D:\CmdRPG-server-next'
$releaseRoot = 'D:\CmdRPG-releases'
git clone https://github.com/Blue-Mount/CmdRPG.git $nextServer
Set-Location $nextServer
git checkout <PR合并后已验证的提交SHA或client-v4标签>
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m tools.release_client --sequence 4 --output $releaseRoot
```

本机验收已使用序号 1–3，因此这里首次生产构建从 4 起。若发布目录已有更高版本，使用下一个序号，或使用 Actions 中已经验证的同一产物。以上构建不会改变运行中的服务器。确认测试通过、所有玩家已退出后：

```powershell
# 使用唯一新备份名；可在旧服务运行时备份。
$backup = Join-Path $oldServer ('backups\before-client-updates-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.sqlite3')
.\.venv\Scripts\python.exe -m tools.backup $backup --database $database
# 在原服务窗口 Ctrl+C，或停止已核对的服务管理器任务；确认旧进程已停止。
# 仍只运行一个 worker，复用原用户的 server-token.dpapi 和原数据库。
.\tools\start-server.ps1 -Database $database -ClientReleases $releaseRoot
```

`-Database` 显式路径必须存在，避免拼写错误产生空存档。不要复制或覆盖原数据库。新代码未更改游戏规则、存档格式或数据库迁移；服务端更新仍须单独重启。启动后从另一窗口只读核对：

```powershell
Invoke-RestMethod https://node.taildf6f41.ts.net/health
Invoke-RestMethod https://node.taildf6f41.ts.net/client-updates/channel.json
# 在已有玩家目录运行 Check-Update.cmd，再 Play.cmd 查看原周目的地图和队伍后退出。
```

若服务启动失败，保留原数据库，停止新服务，用原 checkout/原服务管理方式重启旧服务。不得同时启动两个服务，不应覆盖原存档来“回滚”。客户端新发布也需要继续兼容旧服务，或先完成服务端兼容部署再发布。

## 验证范围

基线为 148 passed / 63 subtests。新增测试使用真实 TCP 发布服务器、真实不可变文件版本和独立游戏 API/临时 SQLite，覆盖无更新、代码/资源/启动器更新、删除资源退出使用、下载中断、损坏/缺失文件、非法路径、Python 加载失败、原子提交失败、网络故障回退、最低版本阻止连接、OS 跨进程锁/崩溃释放、首次迁移、可分发安装包及启动入口、配置/未确认请求字节保留、升级后同 ID 回执恢复及再进程续接。生产 HTTPS 只做健康、鉴权和既有周目读取。
