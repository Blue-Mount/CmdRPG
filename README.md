# CmdRPG：四人小队终端战术游戏

六职业任选四人，固定成长到8级，完成十四场战役与两阶段最终Boss。四条完整路线组合、十六个手工遭遇节点，含胜利自动恢复、金币、商店、职业专属战利品、装备对比与准确战报。纯本地运行，仅依赖Python 3.11+标准库，SQLite逐动作自动存档。

## 立即游玩

```powershell
# 新战役；默认战士、武僧、法师、牧师
python -m client --campaign --seed 42
# 新职业阵容：战士、游荡者、邪术师、牧师；允许重复职业
python -m client --campaign --classes fighter,rogue,warlock,cleric --seed 42
# 六职业中文选角后建队
python -m client --campaign --setup
# 恢复最近周目
python -m client
```

已有开发环境也可用 `.\.venv\Scripts\python.exe` 代替 `python`，或运行 `./run-local.ps1`。

**选择角色 → 选择中文行动 → 选择目标/目的地 → 查看正式预览 → 输入 `c` 确认**。`0`返回，`q`退出；离开不会自动推进。非法输入和取消不会扣费或掷骰。

带环法术有多个环级时，选技能后先选择要消耗的环级；列表显示剩余法术位与本环实际效果。飞弹/射线增加段数，火球增加伤害骰，治疗增加治疗骰，祝福增加目标上限，援助提高生命增益；法师护甲等升环无增强会明确说明。只有一种契约环级的邪术师直接使用该环，戏法无需选择。

支持的交互终端默认显示固定区域决策卡（至少110列×36行），小终端和管道使用文字菜单。`--ui cards` / `--ui plain`可显式选择；`n/p`翻决策列表，`]/[`翻结果。`rules`查看技能，`result`重看结算，`details`查看全部计算；`map`、`party`、`log 10`、`growth P1`、`report`按需查询。

## 奖励与商店更新

前13场胜利都有升级或装备收益：保留第1/3/5/7/9/11/12场升级，在第2/4/6/8/10/13场发职业装备，另保留第5场要塞专武。战斗独占装备共28种，与商店在物品ID和实际属性上均不重复；已拥有的不重复发放，不自动换装。

商店共48件商品，按第1/2/4/6/8/10/12/13场八批上新。武器铺、防具铺、奥术器具店、戒指铺、鞋靴铺、补给站六类分店：战后16选择队员后默认看全部商品，按`t`切换分店；显示本场新货与下次补货，旧货持续供应。

**胜利自动全恢复**：每场金币比原奖励少20，全队生命、法术位、职业资源自动补满并复苏，药水补至至少两瓶；移除营地、短休和法师奥术回想操作。换装提高资源上限后，出发前会免费补满，不重复扣费。

当前内容版本为`0.8.0-auto-rest / 0.8.0-campaign`。十四站旧周目未结算节点应用新奖励，已领取不追发；旧五站/七站沿用原商店并使用自动恢复。旧胜利结算出发前免费恢复，不改原金币。详见[奖励、商店与自动恢复验收](docs/economy-2026-10-29.md)。

## 六职业与十四站扩展（0.7.0历史）

- **邪术师**：魅力魔能爆，5级起两束可逐束分配；痛苦魔爆逐束加魅力伤害；邪魔祝福提供不叠加的临时生命。契约法术位数量少、自动使用高环、胜利自动全恢复，能施放虚假生命、粉碎音波、灼热射线、火球与枯萎术。范围法术会伤及友军。
- **游荡者**：短弓或灵巧近战武器，命中满足条件时自动追加偷袭；2级附赠疾走/撤离，3级快手提供两个附赠动作，7级闪避。目标预览明确偷袭是否可用；近身射箭仍有劣势。隐匿和双持不在本包。
- **十四场、十六节点**：增加蓄池、铸场、调度场/采场、书库、观测台、堡垒、圣所；新敌人包括有限治疗的修复师、双击近卫与范围施法者。保留歼灭目标，不预告敌人未来指令。
- **成长与装备**：从逐场快速跨级改为分布在十四场中的七次升级，第12场胜利达到8级，最后两战使用完整构筑。新短弓/细剑与防御法杖提供取舍，要塞新增两职业独占奖励。
- **可靠性**：旧五站/七站周目继续原路线；新周目才进入十四站。新增“敌人占位时仍能近战接敌”的地图检查，并固定范围结算顺序，保持SQLite重启前后的随机数与事件一致。

原扩展范围、评分口径与当时验证结果见 [0.7.0扩展验收](docs/expansion-2026-10-29.md)。这是一套明确缩减的BG3方向规则，不是完整职业或指定build复刻。

## 战役与恢复

前哨 → 旧道/山脊 → 蓄池 → 铸场 → 要塞 → 调度场/采场 → 书库 → 熔炉 → 观测台 → 堡垒 → 断桥 → 圣所 → 大厅 → 最终Boss。

战后13/14选择路线、16商店、7–10成长与配装、17全队成长记录、18战报、1查看自动恢复说明。胜利结算自动完成恢复与金币净奖励，直接购物、配装或前进即可。

地图每格1.5米；移动按合法路径、射程按三维距离，同高度斜角相邻可近战。高台、墙体、危险和困难地面有真实效果。尸体不占格。战败用 `retry`恢复原战前血量、资源、路线和随机数。

## 少量时间快速体验

以下入口创建独立临时存档，退出后显示的路径可恢复，不读取正常玩家进度。独立战胜利后11号沿用配装重新演练。

```powershell
# 八级新阵容，直接体验双印圣所
.\.venv\Scripts\python.exe -m tools.dev_play --encounter sanctum --classes fighter,rogue,warlock,cleric --ui cards
# 六职业任选四人，从1级开始完整战役
.\.venv\Scripts\python.exe -m tools.dev_play --campaign --classes fighter,rogue,warlock,cleric --ui cards
# 精确指定等级体验某场；--encounter支持全部十六节点
.\.venv\Scripts\python.exe -m tools.dev_play --encounter archive --level 4 --classes fighter,rogue,warlock,cleric
```

## 自动验收与开发

```powershell
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
./.venv/Scripts/python.exe -m pytest -q
# 七阵容×两条关联路线×两种子；全程内存、合法命令，不操作玩家存档
./.venv/Scripts/python.exe -m tools.audit_campaign --output .qa/campaign-audit.json
# 精确选择两个分支，检查交叉路线
./.venv/Scripts/python.exe -m tools.play_campaign --route road --branch quarry --classes fighter,rogue,warlock,cleric
```

自动通关用于流程、恢复和数据比较。真人爽感与五小时节奏不能靠通关率证明。结构：`core/`规则、`content/`内容、`saves/`存档、`client/`中文终端、`tools/`试玩/验收/同步、`tests/`回归。

## GitHub换机传档

先退出游戏，再手动同步：`./run-local.ps1 -UploadSave`上传；另一台电脑先更新代码，再执行 `./run-local.ps1 -DownloadSave`下载，随后 `./run-local.ps1`继续。也可 `python -m tools.sync_saves push` / `pull`。需要Git与仓库读写权限，复用本机Git登录；存档在独立 `game-saves` 分支。

下载前自动备份旧档，进度分歧拒绝静默覆盖；明确采用来源时使用 `-Replace` / `--replace`。断网可本地游玩。

- [战役与经济](docs/campaign.md)
- [成长与装备](docs/growth.md)
- [终端操作](docs/terminal.md)
- [规则来源与差异](docs/rules.md)
- [实施路线](docs/roadmap.md)
- [本地运行与换机](docs/deployment.md)
- [历史验证记录](docs/verification.md)
