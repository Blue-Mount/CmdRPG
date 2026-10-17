# 异机开发与服务器部署

本仓库包含服务器、规则核心、内容、终端客户端和测试。开发电脑可以完整克隆项目；实际游戏服务继续由原电脑运行。克隆代码不会同步服务器存档或凭据。

## 开发环境

需要 Python 3.11+，在仓库根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

开发时使用独立测试数据库，不要通过真实服务器验证会改变玩家进度的动作。纯本地试玩可以执行 `python -m client --new`，产生的 `data/` 只留在本机。

## 原电脑服务器

服务器使用 `server.app:app`，单 worker、回环端口 8765，由 Tailscale Serve 提供私有 HTTPS 入口。Windows 启动脚本为 `tools/start-server.ps1`，从当前用户的 `%LOCALAPPDATA%\CmdRPG\server-token.dpapi` 解密凭据。该文件绑定本机 Windows 账户，不应提交或拷贝作为另一台电脑的凭据文件。

真实存档为原电脑的 `data/game.sqlite3`。规则或内容更新前用 `python -m tools.backup <新的备份路径>` 创建一致性备份。不要覆盖数据库、客户端连接记录或未确认请求记录。推送 GitHub 不会自动改变正在运行的服务；服务器部署须单独完成，且不应在正在游玩时直接替换其代码。

客户端依赖标准库，通过 `CMDRPG_TOKEN` 传入凭据；非回环地址必须使用 HTTPS。Tailscale 私有域名应绕过本机 HTTP/HTTPS 代理，同时保留证书验证。

## 待完成：客户端自动同步

用户已要求启动客户端时自动同步所有运行所需文件，保留本机加密凭据、连接记录、周目 ID 和未确认请求记录。目前尚未实现；已有客户端压缩包是手动快照，不能自动更新。另一台电脑上的自定义启动脚本也尚未纳入本仓库。

实现时需提供一次性的旧客户端升级入口、版本检查、下载完整性验证及失败恢复；明确客户端资源的发布范围，排除服务器存档、凭据、日志、虚拟环境和临时文件。服务端规则/API/存档兼容性与客户端文件更新应分别处理。
