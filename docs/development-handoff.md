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

## 客户端自动同步

已实现独立玩家安装、Windows DPAPI 启动入口、HTTPS 显式清单发布、不可变版本目录和原子更新。开发仓库不得用作玩家目录；旧手动客户端通过一次性安装工具迁移连接及未确认请求，凭据继续使用本机 DPAPI 缓存。

详见 [客户端发布、迁移和服务器部署步骤](client-updates.md)。Git 推送不部署生产服务；标签 CI 仅生成发布产物，原电脑管理员单独部署 HTTPS 发布入口及兼容性门槛。服务端规则/API/存档兼容性仍与客户端文件更新分别处理。
