# M1/M2 实现契约

本文件供并行开发使用。首个切片只开放战士、武僧、法师、牧师的 1 级模板，地图为一场原创前哨战。不把切片宣称为完整 BG3 或六职业首版。

## 核心接口

`core.engine.Engine()` 加载 `content/pack.json`。标准库核心，不依赖 FastAPI。

- `new_run(name="远征队", templates=None, seed=None) -> dict`：返回可 JSON 序列化的完整服务端状态；恰好四人，允许重复职业。
- `view(state) -> dict`：只返回玩家可见内容，包含 `id`, `name`, `revision`, `phase`, `round`, `active`（可操作角色编号列表）, `party`, `enemies`, `map`, `pending`, `recent_events`, `outcome`, `content_version`。隐藏坐标、随机数、战前快照不会进入此结果。
- `preview(state, command) -> dict`：不修改状态或随机数，返回 `command`, `cost`, `description`, `warnings`, `targets`，合法性错误抛 `GameError`。
- `execute(state, command) -> (new_state, events)`：在深拷贝上验证与计算，成功版本加一，失败不改变输入。
- `react(state, choice) -> (new_state, events)`：`choice` 为 `accept` / `decline`，等待窗口原位继续，版本加一。
- `retry(state) -> (new_state, events)`：仅战败可用，恢复战前状态与随机数，版本持续加一。
- `GameError` 包含 `code` 和 `message`，用于 400 / 409 错误转换。
- `core.content.load_content(path=None) -> dict`：内容校验失败抛 `ValueError`。

事件结构为 `{seq, kind, text, details}`。玩家日志不得含隐藏敌人信息；结构化 details 同样必须安全。状态有 `event_log` 最近事件；事件表由持久化层保存。

## 命令对象

```json
{"type":"attack","actor":"P1","target":"E1"}
{"type":"move","actor":"P2","destination":"G6"}
{"type":"cast","actor":"P3","ability":"fire_bolt","target":"E1"}
{"type":"cast","actor":"P4","ability":"healing_word","target":"P1","slot":1}
{"type":"cast","actor":"P4","ability":"bless","targets":["P1","P2","P3"],"slot":1}
{"type":"ability","actor":"P1","ability":"second_wind"}
{"type":"dash","actor":"P2"}
{"type":"disengage","actor":"P2"}
{"type":"dodge","actor":"P2"}
{"type":"help","actor":"P1","target":"P2"}
{"type":"potion","actor":"P1"}
{"type":"end","actor":"P1"}
{"type":"rest","kind":"short"}
```

`actions` / `party` / `inspect` / `log` / `resume` 是读取命令，由客户端基于 view / events 实现；不提交写请求。`preview` 和 `do` 使用相同命令对象。直接命令也经过核心验证。先不实现跳跃、推动、购物、换装或完整战役，不提供这些操作入口。

## 服务 API

`server.app:create_app(database_path=None, token=None)` 工厂；模块级 `app`，环境配置 `CMDRPG_DB`（默认 data/game.sqlite3）、`CMDRPG_TOKEN`；未配 token 限 localhost 部署并在 README 中注明。服务无凭据时不得允许游戏 API，health 可公开；本地 CLI 直接调用仓库，无需 HTTP。

- `POST /runs`：`{name, templates, seed?, command_id}`，幂等建队。
- `GET /runs/{id}/view`。
- `POST /runs/{id}/preview`：`{command}`。
- `POST /runs/{id}/commands`：`{command_id, expected_revision, command}`。
- `POST /runs/{id}/reactions`：`{command_id, expected_revision, choice}`。
- `POST /runs/{id}/retry`：`{command_id, expected_revision}`。
- `GET /runs/{id}/events?after=0&limit=20`：`{events, next_after}`。
- `GET /health`。

写入回执 `{command_id, revision, view, events}`；创建回执另含 `run_id`。相同 ID 不同内容拒绝。重放先于 revision 检查。SQLite 单事务保存状态、事件、回执；按周目串行写。

## 内容包

JSON 顶层：`version`, `rules_version`, `save_version`, `classes`, `abilities`, `items`, `enemies`, `map`, `encounter`。classes / abilities / items / enemies 为以 id 为键的对象。

class 字段：`name`, `subclass`（1 级尚未解锁的亚职注明）、`level`（当前只准 1）、`stats`（str dex con int wis cha）, `save_proficiencies`（两个不同属性）, `hp`, `ac`, `speed`（米）, `attack_stat`, `weapon`（item id）, `abilities`（ability id 列表）, `spell_stat`（可 null）, `slots`（{"1":2}）, `resources`（{"second_wind":1}）。

ability 字段：`name`, `effect`（fire_bolt / magic_missile / mage_armor / sacred_flame / healing_word / cure_wounds / bless / second_wind / unarmed_strike）, `cost`（action / bonus）, `range`（米）, `level`（0/1）, `concentration`（bool 可省略）, `description`。effects 必须属于已实现清单。

item：`name`, `slot`, `kind`, `dice`（武器如 1d8）, `damage_type`, `description`。起始物品不进入未实现掉落池。

enemy：`name`, `hp`, `ac`, `stats`, `speed`, `range`, `dice`, `damage_type`, `role`（melee/ranged），可选 `attack_stat` 与 `save_proficiencies`。

map：`width`（16）, `height`（12）, `cell_size`（1.5）, `walls`（如 F5）, `heights`（格坐标到高度米）, `difficult`（格列表）, `hazards`（坐标到 {damage,description}）, `landmarks`（{position,name,description} 列表）。encounter：`name`, `description`, `party_spawns`（4 个格坐标）, `enemies`（{id,prototype,position}列表）。无同平面多层站点。

## 终端

`python -m client` 默认本地 SQLite；`--url` + `--token` / 环境变量接远程；`--run ID` 恢复；`--new` 新建，`--classes fighter,monk,wizard,cleric`。网络写入的超时重试必须复用原 command_id。Python 标准库 urllib / readline 可选，不让公司端必须装 FastAPI。

`preview move P2 G6`、`preview attack P1 E1`、`preview cast P3 fire_bolt E1`、`do 1`、`end P1`、`react accept`、`retry`、`rest short`。支持数字菜单与中文帮助。
