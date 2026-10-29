# 实现契约（M1/M2历史基础与M3扩展）

M3现行接口见文末；前段保留历史切片契约用于兼容说明。

本文件供并行开发使用。首个切片只开放战士、武僧、法师、牧师的 1 级模板，地图为一场原创前哨战。不把切片宣称为完整 BG3 或六职业首版。

## 核心接口

`core.engine.Engine()` 加载 `content/pack.json`。标准库核心。

- `new_run(name="远征队", templates=None, seed=None) -> dict`：返回可 JSON 序列化的完整游戏状态；恰好四人，允许重复职业。
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

## 本地存档接口

`saves.storage.Repository` 提供 `create_run`、`get_view`、`preview`、`options`、`submit`、`react`、`retry`、`events`、`list_runs` 与 `backup`。中文终端通过 `LocalTransport` 直接调用，同一 SQLite 事务保存状态、事件、操作回执。

GitHub 传档工具 `tools.sync_saves` 使用独立 `game-saves` 分支。游戏和传档共用数据库进程锁；下载先校验再备份、替换，冲突须明确选择来源。详细行为见 [本地运行与传档](deployment.md)。

## 内容包

JSON 顶层：`version`, `rules_version`, `save_version`, `classes`, `abilities`, `items`, `enemies`, `map`, `encounter`。classes / abilities / items / enemies 为以 id 为键的对象。

class 字段：`name`, `subclass`（1 级尚未解锁的亚职注明）、`level`（当前只准 1）、`stats`（str dex con int wis cha）, `save_proficiencies`（两个不同属性）, `hp`, `ac`, `speed`（米）, `attack_stat`, `weapon`（item id）, `abilities`（ability id 列表）, `spell_stat`（可 null）, `slots`（{"1":2}）, `resources`（{"second_wind":1}）。

ability 字段：`name`, `effect`（fire_bolt / magic_missile / mage_armor / sacred_flame / healing_word / cure_wounds / bless / second_wind / unarmed_strike）, `cost`（action / bonus）, `range`（米）, `level`（0/1）, `concentration`（bool 可省略）, `description`。effects 必须属于已实现清单。

item：`name`, `slot`, `kind`, `dice`（武器如 1d8）, `damage_type`, `description`。起始物品不进入未实现掉落池。

enemy：`name`, `hp`, `ac`, `stats`, `speed`, `range`, `dice`, `damage_type`, `role`（melee/ranged），可选 `attack_stat` 与 `save_proficiencies`。

map：`width`（16）, `height`（12）, `cell_size`（1.5）, `walls`（如 F5）, `heights`（格坐标到高度米）, `difficult`（格列表）, `hazards`（坐标到 {damage,description}）, `landmarks`（{position,name,description} 列表）。encounter：`name`, `description`, `party_spawns`（4 个格坐标）, `enemies`（{id,prototype,position}列表）。无同平面多层站点。

## 终端

`python -m client` 恢复本地 SQLite；`--run ID` 恢复指定周目；`--new` 新建，`--classes fighter,monk,wizard,cleric` 配置职业。只依赖 Python 标准库。

`preview move P2 G6`、`preview attack P1 E1`、`preview cast P3 fire_bolt E1`、`do 1`、`end P1`、`react accept`、`retry`、`rest short`。支持数字菜单与中文帮助。


## 2026-10-17 兼容字段扩展

`view.party[*].status` 为统一中文状态；`effects` 包含效果来源、名称及按施法者列出的 `durations`。`concentration` 增加中文 `name`。旧消费者可忽略新增字段。

`initiative_order` 仅提供已揭示参与者编号与当前组标记，不提供先攻骰或未发现敌人。`last_seen` 是写入期间记录的历史目击位置/回合，不是当前位置；查询视图不更新记忆、不推进随机数。`battle_summary` 是保存的事件统计，旧存档从升级后开始累计，不能视为旧战斗的完整历史。非 combat 阶段 `active` 恒为 `[]`。

`Repository.list_runs()` 返回最近周目的编号、名称、版本与更新时间；`--list-runs` 只读，不改变恢复目标。


## M3接口扩展（2026-10-18，现行）

`Engine.new_run(..., *, levels=None, loadouts=None)` 的额外关键字仅供开发创建器；普通建队入口不接受等级/装备注入。新局默认仍为四名1级角色，但使用M3快照。开发创建器可为四人各指定1–5级并验证战前装备/准备；后续仍走同一SQLite命令事务。

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


## A/B增量接口（2026-10-23）

- 飞弹/射线兼容旧`target`命令（全部段给同一目标），新增`targets`按段列表，允许重复，长度必须等于实际段数。预览`allocations`给每段`index/target/hit_probability/damage_dice/damage_bonus/resistant`；不同目标不合成单一命中率。目标中途被击败时其剩余段跳过，其他目标继续。
- 火球二选一：`target`（可见敌人中心）或`destination`（地图内、射程和视线合法的非墙格）。预览`centre/radius/damage_dice/saves`只含可见受影响目标。空落点合法，实际爆炸结算隐藏目标，但事件与视图不泄露。
- 新增`{"type":"ability","actor":"P2","ability":"stunning_strike","target":"E1"}`。正式`cost`只包含确定消耗，`conditional_cost={"ki_on_hit":1}`表示仅命中花气。可使用额外攻击余次。震慑记录于`effects.stunned`，按来源分别持久化到期回合，`conditions`对可见角色显示`stunned`。
- `menu_actions.target_mode`增加`allocation/ground`，`options.max_targets`表示分配段数；原`enemy/party/multi/move/none`兼容。`ground`同时保留可见敌人中心候选。
- buy/equip预览新增`equipment_comparison={before,after,warnings,effect}`，只做脱离目标的装备数值和限制对比；具体命中/豁免仍走战斗preview。双手互斥的购买对比明确提示卸下假设，实际equip继续拒绝冲突。
- 内容新增装备加值白名单、基础武器熟练引用、商店解锁阶段、按职业装备奖励；敌人可选`resistances`与`spell={save,dc}`。熔火箭使用原射程/伤害字段、豁免成功减半；不输出未来指令。
- 不变更SQLite外壳或既有存档版本标记。已存角色、地图、资源与待反应保持；新能力随新建/显式成长快照获得；装备奖励仅首次结算时发放，已结算旧关不追发。既有已进入的熔炉敌人不会自动替换为新模板，新建战斗使用更新内容。切换代码版本前仍可用原备份工具。


## 0.5增量契约（2026-10-26，覆盖上文旧上限）

等级上限8，四职业各8份固定快照，法术位与recover支持1–4环。装备槽扩展ring/feet，accessory不参与护甲熟练限制；旧equipment的缺失槽只在视图中补None，显式装备/成长时再持久化。装备save_bonus同时进入概率与真实属性豁免。

独立/战役内容版本为0.5.0-m5 / 0.5.0-campaign，规则0.5.0-provisional；显式兼容历史0.1切片、0.3独立和0.4战役精确组合，只读不迁移。成功写操作更新外层标记，保留内嵌旧地图/角色/战前快照。

角色growth_history保存逐级收益，last_growth保存最近一次跨级变化；成长预览、战役自动升级与中文growth只读查询复用相同收益生成逻辑。旧档历史不补造。

敌人可配置boss阈值/范围/伤害骰/DC，达到阈值且存活时触发一次，boss_phase随状态持久化。转阶段不重置预算/控制，实际动作继续由命令和反应驱动。待反应与retry沿用同一事务、RNG及幂等回执，不新增后台计时。

## 0.7六职业、十四站与升环（2026-10-29，覆盖历史范围）

新增`rogue`/`warlock`，六职业各八份快照。装备允许明确的`range`与`finesse`；普通武器攻击、预览、优劣势与接敌共用这些字段。短弓不能借机攻击，也不能解锁武僧武艺。偷袭和邪魔祝福为真实被动，快手改变附赠预算，契约位升级跨环保留已消耗数量。

`menu_actions`的带环法术新增`slot_options`，逐项为`slot / remaining / available / upcast / effect`。客户端按环级列出选择，仍提交原`cast`命令的`slot`整数；戏法不带此字段。正式options重新生成对应目标数与段数，preview与execute共用合法性、扣费和效果；客户端不掷骰或推演隐藏目标。

新敌人可配置`attacks: 1–2`、`support: {dice,uses,range}`，`spell`可额外指定`name/radius`。治疗次数持久化，范围可伤及敌友。所有范围结算按角色ID排序，避免JSON/SQLite重排对象改变RNG。

新战役edition3十四场；edition2七场保留在`previous_nodes`，缺省edition1五场保留在`legacy_nodes`。外层内容为`0.7.0-expansion / 0.7.0-campaign`，规则`0.7.0-provisional`，SQLite与JSON格式仍为1；精确兼容0.6及历史组合。存档不做隐式升级，成功写入才更新外层版本。

内容加载要求每个敌人占位时仍存在安全可达的近战触及格。默认加载缓存以完整三份内容文档作为键，每次返回独立深拷贝；显式加载测试文件重新验证，不共享可变内容，不以文件时间戳判断是否变更。

## 0.8奖励、商店与自动恢复（2026-10-29，覆盖历史休整规则）

外层版本为`0.8.0-auto-rest / 0.8.0-campaign`，规则`0.8.0-provisional`。精确接受0.7.0及此前兼容组合；SQLite/JSON格式仍为1。读取和预览不迁移；成功写操作更新版本。

胜利事务按原奖励减20金币、自动成长、发放装备、恢复全队，最后一次提交；同一结算不会重发或重扣。独立局自动恢复不收金币。恢复复苏全队、补满生命/法术位/职业资源、清除临时效果及专注、药水补至至少2瓶并保留超额库存。`automatic_recovery`保存版本、扣减、旧档标记和成员前后记录；事件发生在胜利阶段，不计入战中治疗。失败与retry保持原战前快照语义。

新法师快照删除`arcane_recovery`，旧档只读视图隐藏它，恢复时移除旧资源。`rest/camp/recover`明确拒绝且不写入。旧胜利出发或战后装备提高资源上限时，travel在新战前快照建立之前免费补满，不再扣20。

`campaign.equipment_awards[node][actor]`保存实际发放ID，结算新增`equipment_rewards`，不以现行奖励表伪造旧档已领取记录。十四站前13场全职业均有等级或装备；战斗装备与商店按物品ID及实际属性签名双重隔离，并拒绝跨场重复奖励。

商店新增`shop_types/shop_categories`；视图商品增加`shop_type/shop_name/unlock_stage/new`，战役增加`shop_types`计数及`next_restock`。同一正式buy接口执行所有分店购买，筛选仅影响展示。五站/七站采用保存的旧商店价格和解锁表；十四站采用八批48件新表。
