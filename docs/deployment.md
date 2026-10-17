# 启动、备份和恢复

以下命令在项目根目录的 PowerShell 中执行。远程入口与 ThiefBook 的实际部署尚未核对；当前实现独立游戏服务、凭据和 SQLite 文件。M1/M2 切片的家中与公司网络连接仍需在实际设备上验证。

## 本地运行

```powershell
python -m client --new
```

本地终端直接使用 SQLite 和规则核心，运行时不需要 FastAPI，也不需要 token。默认存档在 `data/game.sqlite3`。使用同一个数据库时，本地客户端与 API 都按版本号提交，避免旧局势覆盖新操作。

## API 服务

安装服务依赖并配置 token：

```powershell
python -m pip install -r requirements.txt
$env:CMDRPG_DB = 'data/game.sqlite3'
$env:CMDRPG_TOKEN = python -c "import secrets; print(secrets.token_urlsafe(32))"
python -m uvicorn server.app:app --host 127.0.0.1 --port 8765 --workers 1
```

`CMDRPG_DB` 默认是 `data/game.sqlite3`；`CMDRPG_TOKEN` 无默认值。没有 token 时，只有 `/health` 可用，所有游戏 API 拒绝访问。凭据不要写入版本库或战斗日志。首次配置凭据后，使用进程管理器时也要设置相同环境变量和项目工作目录。只运行一个服务进程；主机重启后重新启动即可恢复最近一次完整提交，待选反应不会自行推进。

本机 HTTP 验证：

```powershell
Invoke-RestMethod 'http://127.0.0.1:8765/health'
python -m client --url http://127.0.0.1:8765 --new
```

客户端读取 `CMDRPG_TOKEN`，也可以使用 `--token`。远程使用经过核对的 HTTPS 入口转发到这个回环端口，客户端把 `--url` 改为实际地址。公司的网络策略与 Python 运行环境需要单独实测。尚未创建 Tunnel、修改路由或配置系统开机服务。

## 一致性备份

可以在服务运行时备份。工具使用 SQLite backup API，包含已经提交到 WAL 的数据，不使用运行期间直接复制主数据库文件的方式。备份自带数据库完整性检查，目标文件必须不存在，避免覆盖旧存档。

```powershell
python -m tools.backup backups/game-before-update.sqlite3
python -m tools.backup backups/game-manual.sqlite3 --database data/game.sqlite3
```

备份包含完整服务端信息和命令回执，应放在只有自己可访问的位置。更新规则、内容包或程序前备份，同时记录当前程序版本。存档保存 `content_version`、`rules_version`、`save_version`；不兼容内容由核心拒绝恢复。当前切片没有自动迁移或旧内容包归档功能，恢复时需要匹配的代码与内容包。

## 恢复到独立文件

先停止原服务，保留原数据库及其 `-wal`、`-shm` 文件。将备份复制到一个新的恢复目录，文件名不要与已有存档相同：

```powershell
New-Item -ItemType Directory -Path restore-check -ErrorAction Stop
Copy-Item -LiteralPath backups/game-before-update.sqlite3 -Destination restore-check/game.sqlite3 -ErrorAction Stop
$env:CMDRPG_DB = 'restore-check/game.sqlite3'
python -m uvicorn server.app:app --host 127.0.0.1 --port 8765 --workers 1
```

确认恢复文件不存在后执行复制；以上新目录命令在目录已存在时会停止，不复用原数据库。保持正确的 `CMDRPG_TOKEN`。用原周目 ID 连接查看版本、资源、事件和待选反应；客户端重发已有命令 ID应取得原回执。确认恢复无误后，可以持续使用恢复文件，不需要覆盖原存档。

自动测试覆盖运行中 WAL 备份、独立文件恢复、待选反应、完整回执重放，以及提交回执前故障导致的事务回滚。实际网络连接和系统重启操作仍需部署时验证。
