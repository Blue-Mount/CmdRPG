# 实现契约（M1/M2历史基础与M3扩展）

M3现行接口见文末；前段保留历史切片契约用于兼容说明。

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


## 2026-10-17 兼容字段扩展

`view.party[*].status` 为统一中文状态；`effects` 包含效果来源、名称及按施法者列出的 `durations`。`concentration` 增加中文 `name`。旧消费者可忽略新增字段。

`initiative_order` 仅提供已揭示参与者编号与当前组标记，不提供先攻骰或未发现敌人。`last_seen` 是写入期间记录的历史目击位置/回合，不是当前位置；查询视图不更新记忆、不推进随机数。`battle_summary` 是保存的事件统计，旧存档从升级后开始累计，不能视为旧战斗的完整历史。非 combat 阶段 `active` 恒为 `[]`。

鉴权 `GET /runs` 返回最近 20 个周目的编号、名称、版本、更新时间；客户端 `--list-runs` 只读，不更改默认恢复目标或处理 pending journal。


## M3接口扩展（2026-10-18，现行）

`Engine.new_run(..., *, levels=None, loadouts=None)` 的额外关键字仅供开发创建器；普通HTTP创建接口不接受等级/装备注入。新局默认仍为四名1级角色，但使用M3快照。开发创建器可为四人各指定1–5级并验证战前装备/准备；后续仍走同一SQLite和HTTP命令事务。

新增安全阶段命令（`preview`与`commands`使用完全相同对象）：

```json
{"type":"levelup","actor":"P1"}
{"type":"equip","actor":"P1","slot":"off_hand","item":null}
{"type":"equip","actor":"P1","slot":"main_hand","item":"greatsword"}
{"type":"prepare","actor":"P3","spells":["magic_missile","fireball"]}
{"type":"recover","actor":"P3","slot":2}
```

levelup只能升一级、最高5，不允许变更职业或属性；本轮是安全阶段成长验证，没有经验奖励条件。equip验证背包、装备位、双手/盾互斥；不熟练可穿戴，但结算施法限制与攻击劣势。prepare只接受无重复已学带环法术，上限等级+施法属性修正，领域恒定法术必须保留且不计入上限。recover消耗奥术回想点数恢复一个指定环法术位。战斗/反应等待时全部拒绝，失败不改变输入。

新增能力id：`action_surge`, `rally`, `flurry`, `patient_defence`, `scorching_ray`, `fireball`, `aid`, `preserve_life`, `mass_healing_word`。`action_surge`使用 `cost=free` 元数据和职业资源，不扣动作预算；其他按JSON声明扣动作/附赠、职业资源和法术位。群体援助/维持生命/治愈真言使用无目标命令，预览列出实际可见合法目标；火球指定中心敌人；射线/飞弹仍单目标。攻击预览可包含 `attack_count` 和 `hit_probability`，火球给出 `save_dc`。

party投影增加 `equipment`, `equipment_names`, `inventory_items`, `proficiency`, `extra_attack`, `max_slots`, `max_resources`, `known_spells`, `prepared`, `always_prepared`；不返回内部随机数。原有消费者可忽略新字段。额外攻击余次存在角色存档中，不能用于施法或借机反应；重启、反应中断后保留，开始回合归零。

开发局额外投影 `development=true`，胜利时可预览/确认 `{"type":"practice"}`：沿用等级、装备与准备法术，刷新生命/药水/全部资源，在同一开发周目演练原前哨战。该命令在普通周目不可用；不是战役下一关，不给经验/奖励。继续使用原有command_id幂等、revision冲突和回执恢复，不经SQL改档。

### 存档兼容策略

SQLite表结构和JSON外壳仍为1；M3新局标记内容 `0.3.0-m3`、规则 `0.3.0-provisional`，角色 `growth_version=1`。旧的精确组合 `0.1.0-slice/0.1.0-provisional/1` 由显式兼容分支读取，view/preview不迁移或发放资源；原HP/AC/法术/内嵌地图与战前快照保持原值，可继续战斗及待反应。旧角色只在安全阶段显式levelup时应用下一等级并建立装备/准备数据，同时将周目标记为M3内容版本；其他尚未升级的旧角色仍按原字段兼容。未知版本或未知growth_version拒绝。

升级在副本中计算，由Repository在单事务提交，原回执仍可重放。升级不自动备份/覆盖原库；部署前应使用已有一致性备份工具。已显式升级的M3档不能回退给旧二进制，须恢复升级前备份。开发试玩一律独立临时数据库，不能通过改生产存档设定等级。
