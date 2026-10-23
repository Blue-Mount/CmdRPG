# CmdRPG：四人小队终端战术游戏

目前包含 M3 四职业1–5级成长与 M4 五站战役雏形：路线分支、六张遭遇地图、营地、金币奖励、药水/装备商店和最终 Boss。游戏在本机运行，使用 SQLite 自动存档；可手动通过 GitHub 换机传档。六职业八级、20场完整版仍是长期目标。

## M4 战役试玩

在仓库根目录执行，使用独立临时存档，启动时会显示恢复命令：

```powershell
.\.venv\Scripts\python.exe -m tools.dev_play --campaign
```

也可 `python -m client --campaign --seed 42` 在正常本地数据库新开战役。旧周目保留；现有独立战斗首页9或战后12也能新开战役。

路线为 **前哨 → 旧道或山脊 → 要塞双门 → 熔炉 → 裂隙之心 Boss**。战后13/14前进、15营地、16商店、7–10换装/准备法术，均先预览再按 `c`。胜利自动发金币并解锁成长；生命与资源消耗带入下一站。完整说明见 [战役规则](docs/campaign.md)，验证记录见 [M4 QA](docs/qa-m4-2026-10-18.md)。

默认在支持的交互终端（至少110列×36行）使用固定区域决策卡：地图、角色/行动、预览和真实结算分区显示；小终端与管道自动使用文字菜单。`--ui cards` 选择卡片，`--ui plain` 保留原文字入口。卡片中 `n/p` 翻阅完整决策列表，`]/[` 翻阅完整结果；`rules` 展开所选技能，`result` 重看结算，`details` 展开本次全部计算。`map`、`party`、`log 10` 等原命令继续可用。多段攻击、伤害、击杀和状态事件按原顺序完整保留。

直接体验5级四职业卡片战斗（独立临时存档）：

```powershell
.\.venv\Scripts\python.exe -m tools.dev_play --level 5 --ui cards
```

## A/B：战斗自由度与装备奖励

飞弹/灼热射线可逐段分配目标，火球可选择地面爆心；5级武僧新增震慑拳，队友可利用攻击优势与自动失败的敏捷豁免配合。熔炉增加火抗守卫和豁免型术士。9件新装备支持四职业的攻守/机动/治疗取舍，商店按进度补货；购买和换装直接显示属性、熟练与副作用对比，要塞胜利发职业装备奖励。

反复试玩5级战斗切片（独立临时存档，胜利后11号重新演练）：

```powershell
.\.venv\Scripts\python.exe -m tools.dev_play --battle-slice --ui cards
```

目标分配输入如 `1,1,2`；地面火球直接输入 `G6`。详见 [A/B实现与验证](docs/qa-combat-rewards-2026-10-23.md) 和 [终端玩法](docs/terminal.md)。

## M3独立开发试玩

在仓库根目录执行，数据库与客户端记录自动放到独立临时目录，退出后保留路径供恢复；不读取正常玩家存档或凭据：

```powershell
.\.venv\Scripts\python.exe -m tools.dev_play --level 5
.\.venv\Scripts\python.exe -m tools.dev_play --levels 2,3,4,5 --equip P1:off_hand:none --equip P1:main_hand:greatsword
```

`--classes` 选择四人职业。`party` 查看中文装备、属性、成长、法术位和职业资源。胜利后7–10号进入各队员成长/装备/准备菜单，先预览再用 `c` 确认；开发局11号沿用新等级与装备重新演练，并刷新全部生命与资源。不是战役下一关或奖励。启动时输出该局的独立恢复命令。完整能力和规则差异见 [M3成长](docs/growth.md)。

## 立即试玩

需要 Python 3.11 或更高版本，在本项目目录执行。**本地玩法仅依赖 Python 标准库**。

```powershell
python -m client --new --seed 42
```

默认队伍为战士、武僧、法师、牧师；允许重复职业。首页显示文字地图与四名队员，标明谁能操作。输入角色编号后就能看到普通行动和该角色全部职业技能，包括中文名称、作用、成本、剩余资源及不可用原因。

按屏幕上的编号游玩：**选择角色 → 选择中文行动或技能 → 选择目标或目的地 → 自动预览 → 输入 `c` 确认**。数字只选择当前列表，子菜单中 `0` 返回上一层，`q` 退出。攻击、治疗和施法的目标列表显示生命、距离、命中率或豁免概率；祝福可输入 `1,2,3` 选择多个目标。反应窗口也先选择，再用 `c` 确认。

新周目先选 **3 号法师**，选“移动”，输入 `G6` 或选择常用目的地并确认，然后选“火焰箭”和敌人编号。法师从初始位置射向断墙另一侧会被遮挡；目标菜单会直接说明，移动菜单会展示真实路径、可达范围、危险与借机攻击。普通行动后保留选中的角色，选择“结束操作”并确认后自动切换到下一位可操作队员。

已有命令仍可作为快捷入口：`actions P3` 看职业技能，`party` 看资源，`map` 看地图，`inspect G6` 查位置，`log 10` 按需展开计算明细；详见 [终端玩法与命令](docs/terminal.md)。默认界面无需记忆英文命令或能力编号。

输入 `q` 随时离开，再执行 `python -m client` 恢复最近周目。存档位于 `data/game.sqlite3`，终端记住所选存档与周目 ID。待选反应不会因离开而推进；战败用 `retry` 恢复同一战前状态与随机数。

**独立前哨战胜利后进入战后菜单**：`1` 全队短休，`2` 查看队伍，`3` 同阵容重开独立周目，`4` 地图，`5` 日志，`6` 战利品说明，`7–10` 成长与装备，`12` 新开战役。新建操作需要 `c` 确认，原存档保留。独立前哨战不发金币或尸体掉落；战役的奖励自动结算，可在商店购买装备。

地图每格都有明确边界，`P2*` 标记当前选角；尸体另列说明且不占格、不挡路。目标菜单的距离是包含高差的**三维直线距离（同高度近战 / 触及允许斜角相邻）**，移动成本按可走路径另算。例如 K4 台阶到 L4 高台水平相隔 1.5 米、高差 1.5 米，距离约为 2.12 米。

```powershell
python -m client --new --classes fighter,fighter,monk,monk
python -m client --run <周目ID>
```

当前目录已有开发虚拟环境时，也可执行 `.\.venv\Scripts\python.exe -m client --new`，或 `./run-local.ps1 -New`。

## GitHub 存档

平时直接本地玩，每个成功动作自动保存。换电脑时，先退出游戏，再手动同步：

```powershell
# 在有进度的电脑上传
./run-local.ps1 -UploadSave
# 在另一台电脑下载
./run-local.ps1 -DownloadSave
# 继续最近周目
./run-local.ps1
```

也可使用 `python -m tools.sync_saves push` / `pull`。代码通过 `git pull` 更新；存档单独放在同一仓库的 `game-saves` 分支，工具自动处理，无需切换代码分支。需要 Git 和该 GitHub 仓库的读写权限，复用本机 Git 登录。

下载覆盖前自动备份本机旧档；有未上传进度或远端分歧时拒绝静默覆盖。确认采用来源进度时使用 `-Replace`（命令行工具对应 `--replace`）。游玩期间不进行同步，断网可以照常玩。详见 [本地运行与 GitHub 存档](docs/deployment.md)。

## 开发

```powershell
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
./.venv/Scripts/python.exe -m pytest -q
```

结构：`core/` 规则、`content/` 内容、`saves/` 本地存档、`client/` 中文终端、`tools/` 试玩/备份/GitHub 同步、`tests/` 回归验证。运行游戏只需 Python 3.11+ 标准库。

当前为四职业1–5级、五站战役雏形，包含路线分支、营地、金币、商店与最终 Boss。六职业八级、20场战役和三次完整人工试玩仍为长期目标。

- [终端玩法](docs/terminal.md)
- [战役规则](docs/campaign.md)
- [成长与装备](docs/growth.md)
- [规则差异](docs/rules.md)
- [实施路线](docs/roadmap.md)
- [验证记录](docs/verification.md)
