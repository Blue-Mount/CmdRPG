# 游戏 API（M3）

所有游戏接口使用 `Authorization: Bearer <CMDRPG_TOKEN>`。`GET /health` 公开，返回 `{"status":"ok"}`。服务未设置 token 时游戏接口返回 503；凭据缺失或错误返回 401。凭据比较使用恒定时间比较。

普通创建采用四个1级职业模板；M3支持固定成长至5级、装备和准备法术，仍为一场前哨战。HTTP 返回的视图、事件与预览由核心生成，不返回完整存档、随机数状态、种子或战前快照。API 文档可在本地服务的 `/docs` 查看。

| 方法与路径 | 请求 | 返回 |
| --- | --- | --- |
| `POST /runs` | `name`（默认远征队），`templates`（省略采用默认四人），可选 `seed`，`command_id` | 创建回执 |
| `GET /runs/{id}/view` | 无 | 玩家视图 |
| `POST /runs/{id}/preview` | `{"command": {...}}` | 预览及其 `revision` |
| `POST /runs/{id}/options` | `{"command": {...}}`，行动尚未选择目标或目的地 | 可见目标 / 目的地及合法预览、`revision` |
| `POST /runs/{id}/commands` | `command_id`，`expected_revision`，`command` | 写入回执 |
| `POST /runs/{id}/reactions` | `command_id`，`expected_revision`，`choice`（`accept` / `decline`） | 写入回执 |
| `POST /runs/{id}/retry` | `command_id`，`expected_revision` | 写入回执 |
| `GET /runs/{id}/events` | `after=0`，`limit=20`（1 至 200） | `{"events": [...], "next_after": N}` |

写入回执：

```json
{"command_id":"request-uuid", "revision":1, "view":{}, "events":[]}
```

创建回执另含 `run_id`。状态、事件和回执在同一 SQLite 事务中提交；收到成功响应即表示已经保存。事件序号按周目单调增长，分页使用最后一条 `seq` 作为 `after`。

`command_id` 在一个数据库中全局唯一，覆盖创建、动作、反应、战斗重试和不同周目。建议使用 UUID。完全相同的请求重发返回原回执，先于版本检查；即使周目已继续推进，回执中的 `view` 仍是该命令执行后的原始视图。相同 ID 的请求内容、周目或版本不同则返回 409 `command_id_conflict`。

预览不会保存或掷骰。确认命令使用预览返回的 `revision` 作为 `expected_revision`。新请求版本过期返回 409，示例：

```json
{"error":{"code":"revision_conflict","message":"局势已变化，请刷新后重新预览。","current_revision":3}}
```

客户端刷新后重新预览，并为新请求创建新的 ID；网络响应丢失时必须原样重发旧请求和旧 ID。错误命令不会保存回执，不扣资源、不改变随机数。核心合法性错误返回 400，状态或版本冲突返回 409，周目不存在返回 404，HTTP 参数不合法返回 422。鉴权和 HTTP 校验错误使用 FastAPI 的 `detail` 字段，游戏错误使用上述 `error` 字段。

## 中文菜单数据

视图中每个 `party` 条目提供 `menu_actions`：全部普通动作与已经拥有的职业技能，包括当前不可用的技能。每项包含中文 `name` / `description`、半成品 `command`、`cost`、`remaining`、`available`、`reason` 与 `target_mode`（`none` / `enemy` / `party` / `multi` / `move`）。`cost` 与 `remaining` 使用相同资源键，如 `action`、`bonus`、`slot_1`、`potions`、`second_wind`；数值分别为此次成本与剩余资源。移动成本由选择的真实路径决定，角色 `budgets.movement` 给出剩余米数。`multi` 另有 `max_targets`。菜单的技能可用状态依据角色和资源，目标距离、视线与目标类型限制由目标列表说明。

`options` 接受以下半成品命令（不能附加 `target`、`targets` 或 `destination`）：

```json
{"type":"attack","actor":"P1"}
{"type":"help","actor":"P1"}
{"type":"cast","actor":"P3","ability":"fire_bolt"}
{"type":"cast","actor":"P4","ability":"bless"}
{"type":"move","actor":"P2"}
```

返回 `revision`、`command`、`entries`。需要法术位且未显式指定 `slot` 时，菜单自动选择最低可用且合法的环阶。目标项含 `target` / `id`、中文名称、`hp` / `max_hp`、`position`、实际三维 `distance`、中文方向及高差 `distance_description`、`available`、`reason`。合法目标附 `preview`，并按判定方式提供 `hit_probability` 或 `failure_probability`（目标豁免失败概率）；魔法飞弹自动命中为 1。不可用目标仍展示遮挡、射程、失能等原因，隐藏敌人和已死亡敌人不会出现在列表中；已死亡队员仍可显示，说明无法治疗或救助。祝福术先逐个检查目标，玩家选定多个编号后仍须调用 `preview` 检查完整 `targets`。

移动项含 `destination`、地标中文 `name` / `description`、真实路径 `path` 与米数 `cost`，超出本回合预算的地点仍返回路径、成本和原因。`reachable` 仅包含当前可以合法预览执行的目的地；`landmarks` 是同一列表中的命名常用目的地。路径按地形、高差和可见占格计算，合法预览的危险说明与借机攻击条件使用实际执行规则。隐藏占格不提前泄露，实际移动遇到它会保留已发生的移动并停下。

待选反应的 `pending.preview` 提供反应成本、目标与准确命中概率；`pending` 不公开待续接的内部路径。菜单查询与预览均为纯读，不推进版本、不保存回执、不改变随机数。列表中的合法预览可以用来展示信息，执行仍经过同一核心校验与版本检查。

## 战后菜单数据

胜利视图提供 `settlement`，当前内容返回 `completed: true`、`next_encounter: null`、`loot_available: false` 与中文 `loot_description`；战斗中和战败时为 `null`。遗体记录保留在可见 `enemies` 中供查看，客户端依据 `conditions` 移除地图上的死亡敌人，不改变存档。当前没有搜刮命令或战利品系统。

`{"type":"rest","kind":"short"}` 的预览一次作用于全部未死亡队员，提供 `targets` 和 `rest_effects`；每项含 `actor`、`name`、`hp_before`、`hp_after`、`max_hp`、`healing` 及 `resources_restore`（此次恢复数量）。一次提交消耗一个全队短休次数，不恢复法术位和药水，不复活死者。菜单中的“新开一场战斗”经确认后使用现有 `POST /runs` 创建接口，保留当前职业阵容，原周目不变，并复用客户端创建幂等及回执恢复机制。

本地仓库 `server.storage.Repository` 使用相同规则，不依赖 FastAPI；通过 `list_runs()` 可以选取已有周目。仓库通过线程锁与 SQLite 写事务串行提交，同一存档的多个窗口依据版本检查避免覆盖。


M3新命令levelup/equip/prepare/recover/practice及新增字段见 [现行实现契约](implementation-contract.md#m3接口扩展2026-10-18现行)。所有新写入复用现有POST commands、幂等回执与版本保护；不新增绕过验证的开发HTTP写接口。独立试玩的--http仅绑定随机回环端口、临时数据库和一次性凭据，不使用生产配置。
