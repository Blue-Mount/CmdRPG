# 2026-10-17 本机实施验收

- 指定基线 `2992fedc455c78e5d62592753e065d8ff7b8b401`：148 passed，63 subtests。
- 最终回归：174 passed，63 subtests，耗时约 60 秒；另完成服务端 PowerShell 入口语法检查。
- 开发目录 `D:\CmdRPG-dev`，分支 `feat/client-auto-update`，独立 `.venv` / Python 3.12.14，已安装 requirements-dev.txt。
- 原手动客户端 `D:\CmdRPG` 保留；新玩家安装 `D:\CmdRPG-player`；明确清单构建产物 `D:\CmdRPG-releases`。
- 本机先安装客户端序号 1，再通过真实回环 HTTP 发布服务器更新到 2，随后更新到 3。序号 3 修复从 PowerShell 7 经 Python 启动 Windows PowerShell 时的模块路径影响。玩家安装使用版本 3，发布目录保留不可变历史文件。
- 从新版 bootstrap/版本化启动器运行两次独立客户端，复用原 Windows DPAPI 缓存，HTTPS 健康、鉴权、地图/队伍读取、退出再连接均成功，周目仍为 `1208f8dc-9abd-4244-85ba-1132a21adecd`。未新建周目或提交动作，连接配置保持原值，没有新增未确认请求。
- 原电脑尚未部署 `/client-updates`：新版明确显示更新 HTTP 404 原因后使用已验证的本地版本，继续读取原服务器周目。上线自动分发需要原电脑按 client-updates.md 部署发布入口；本次没有修改原电脑进程、规则或存档。
- 自动测试在临时目录运行真实 TCP 下载、两份不同的代码/资源/启动器、独立游戏 API 和 SQLite；额外验证一次性分发包、配置/请求保留、跨更新的同 ID 回执恢复、进程恢复与最低版本拒绝时请求不丢失。
- 当前 Windows 测试环境的 FastAPI TestClient 有一条上游 Starlette 关于 httpx 的弃用警告；测试通过，没有为消除警告改变固定依赖版本。

玩家入口为 `D:\CmdRPG-player\Play.cmd`，只检查更新为 `Check-Update.cmd`。解释器固定为本机已验收的 Codex 缓存 Python；若缓存被清理，可使用长期安装的 Python 3.11+ 重装玩家入口，保留现有 data/ 和 DPAPI 凭据。旧不可变版本暂不自动清理，固定 ABI 转发入口及 Python 升级需要一次性重新安装。普通玩家无需 GitHub 权限。

生产部署命令、发布标签/Actions artifact 流程及回退操作见 [客户端自动更新](client-updates.md)。本机版本已达到 3，原电脑首次重新构建请使用序号 4 或更高，避免跨 checkout 的换行差异造成同序号不同摘要。若直接使用本机已验证、逐字节相同的序号 3 产物则可沿用 3；后续发布继续严格递增。
