# 本地运行与 GitHub 存档

需要 Python 3.11+。首次 `git clone`，已有代码用 `git pull` 更新。在仓库根目录执行：

```powershell
python -m client --campaign
python -m client
```

第一条新开战役，第二条恢复最近周目；`python -m client --new` 新开独立前哨战，`--list-runs` 列出周目。也可使用 `./run-local.ps1 -Campaign`、`./run-local.ps1`。游玩只依赖标准库。

## 存档位置

`data/game.sqlite3` 保存所有周目、随机状态、事件和操作回执；`data/client.json` 记录本机数据库路径与所选周目。每个成功动作自动写入 SQLite，离开游戏不会自行推进回合。可用 `--db` / `CMDRPG_DB` 和 `--config` / `CMDRPG_CLIENT_CONFIG` 指定其他路径。

同一存档同时只允许一个游戏或同步进程使用。`.lock` 文件是正常的锁文件，操作系统会在进程退出后释放锁；无需手工删除。

## 换电脑传档

先退出游戏，使用该私有仓库已有的 GitHub 登录。工具复用 Git 凭据管理，不另外保存 GitHub token。

```powershell
# 电脑 A：上传本地全部周目
python -m tools.sync_saves push
# 电脑 B：先拉最新游戏代码，再下载存档
git pull
python -m tools.sync_saves pull
python -m client
```

一键脚本等价入口：`./run-local.ps1 -UploadSave` 和 `./run-local.ps1 -DownloadSave`。未选择同步时完全本地游玩。工具不会自动上传或下载，也不会改变代码分支、提交开发改动。

存档位于同一 GitHub 仓库独立的 `game-saves` 分支。每次上传用 SQLite backup 创建一致性快照，分支只提交 `game.sqlite3` 与 `save.json`（校验、所选周目、时间）；不上传本机路径、凭据或配置文件。无需手工 checkout 这个分支。首次成功上传会创建分支。

下载校验完整性、逻辑校验值和游戏规则/内容版本，重建本机路径并恢复所选周目。若两台电脑分别推进，工具会提示选择；不会合并两个 SQLite 进度。下载覆盖已有不同存档前，在 `data/save-backups/` 留下完整备份。

确实需要选择一边时显式执行：

```powershell
# 采用 GitHub 进度，备份并覆盖本机不同进度
python -m tools.sync_saves pull --replace
# 采用本机进度，追加 Git 提交覆盖 GitHub 当前进度；历史提交仍保留
python -m tools.sync_saves push --replace
```

普通 push 也不会强制推送；上传中如果其他电脑先提交，Git 会拒绝并提示重新同步。首次遇到一份没有同步记录的本机不同进度，同样需要明确选择。

网络/登录失败时保留本地档。请先在该电脑完成私有仓库的正常 clone/pull 登录。代码版本不兼容时先更新游戏；同步不包含代码更新。GitHub 会保留存档历史，所以只上传你希望保留的周目即可。

## 手动备份与恢复

```powershell
python -m tools.backup backups/game-before-update.sqlite3
# 自定义数据库
python -m tools.backup backups/game-other.sqlite3 --database data/other.sqlite3
```

备份目标须不存在，工具包含已提交 WAL 数据并做完整性检查。恢复时先退出游戏，将备份放在独立路径，再指定它启动；保留原档：

```powershell
python -m client --db backups/game-before-update.sqlite3 --config data/restored-client.json --list-runs
python -m client --db backups/game-before-update.sqlite3 --config data/restored-client.json --run 周目ID
```

不要将运行期间直接复制的单个 SQLite 文件作为备份。规则/内容升级前先备份，旧程序未必能读取新格式存档。
