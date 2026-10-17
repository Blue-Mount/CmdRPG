"""Chinese guided interaction using only server views and legal previews."""

from copy import deepcopy
import re

from .parser import ParseError
from .render import (render_map, render_menu_actions, render_options,
                     render_party, render_preview, render_rules, render_growth)


class TerminalMenu:
    def __init__(self, client):
        self.client = client
        self.selected = None
        self.stage = "home"
        self.action = None
        self.options = {}
        self.entries = []
        self.confirmation = None
        self.return_stage = "actions"
        self.revision = client.view.get("revision")
        self.compact = False

    def sync(self):
        client = self.client
        if client.pending:
            self.stage = "recovery"
        elif client.view.get("pending"):
            if not (self.stage == "confirm" and self.confirmation
                    and self.confirmation.get("operation") == "reactions"):
                self.stage = "reaction"
        elif self.stage in ("reaction", "recovery"):
            self.stage = "actions" if self.selected else "home"
        if (not client.pending and not client.view.get("pending")
                and client.view.get("phase") != "combat" and self.stage not in ("confirm","growth")):
            self.stage = "settlement" if client.view.get("phase") == "victory" else "home"
            self.selected = None

    def actor(self):
        return next((a for a in self.client.view.get("party", [])
                     if a["id"] == self.selected), None)

    def context(self):
        actor = self.actor()
        who = "%s %s" % (self.selected, actor["name"]) if actor else "队伍"
        if self.stage in ("reaction", "confirm") and self.client.view.get("pending"):
            pending = self.client.view["pending"]
            reactor = next((a for a in self.client.view.get("party", []) if a["id"] == pending["reactor"]), {})
            who = "%s %s" % (pending["reactor"], reactor.get("name", ""))
        action = self.action.get("name", "") if self.action else ""
        stages = {"home": "选择角色", "actions": "选择行动或技能", "target": "选择目标",
                  "move": "选择目的地", "confirm": "确认执行", "reaction": "选择反应", "recovery": "恢复回执",
                  "settlement": "战后结算", "growth":"成长与装备"}
        return "【%s%s · %s】" % (who, " · " + action if action else "", stages[self.stage])

    def build(self):
        self.sync()
        client = self.client
        menu = {}
        if self.stage == "home":
            menu = {str(n): ("actor", a["id"]) for n, a in enumerate(client.view.get("party", []), 1)}
            for n, name in enumerate(("map", "party", "log", "help"), 5):
                menu[str(n)] = ("read", name)
            if client.view.get("phase") in ("defeat", "defeated"):
                menu["9"] = ("retry", None)
        elif self.stage == "settlement":
            menu = {"1": ("rest", None), "2": ("read", "party"),
                    "3": ("new_run", None), "4": ("read", "map"),
                    "5": ("read", "log"), "6": ("read", "loot")}
            menu.update({str(n):("growth",a["id"]) for n,a in enumerate(client.view.get("party",[]),7)})
            if client.view.get("development"): menu["11"]=("practice",None)
        elif self.stage == "growth":
            actor=self.actor()
            entries=[]
            if actor['level']<5: entries.append(('固定职业升级',{'type':'levelup','actor':self.selected}))
            for slot in actor.get('equipment',{}):
                if actor['equipment'][slot]: entries.append(('卸下 '+actor['equipment_names'][slot],{'type':'equip','actor':self.selected,'slot':slot,'item':None}))
            for item in actor.get('inventory_items',[]):
                if actor.get('equipment',{}).get(item['slot'])!=item['id']:
                    entries.append(('穿戴 '+item['name'],{'type':'equip','actor':self.selected,'slot':item['slot'],'item':item['id']}))
            for slot in actor.get('max_slots',{}):
                if actor['resources'].get('arcane_recovery',0)>=int(slot) and actor['slots'].get(slot,0)<actor['max_slots'][slot]: entries.append(('奥术回想：恢复'+slot+'环法术位',{'type':'recover','actor':self.selected,'slot':int(slot)}))
            names={x['id']:x['name'] for x in actor.get('abilities',[])}
            for spell in actor.get('known_spells',[]):
                if spell in actor.get('always_prepared',[]): continue
                spells=list(actor.get('prepared',[]))
                if spell in spells: spells.remove(spell); title='取消准备 '
                else: spells.append(spell); title='准备 '
                entries.append((title+names.get(spell,spell),{'type':'prepare','actor':self.selected,'spells':spells}))
            menu={str(n):('manage',entry) for n,entry in enumerate(entries,1)}
        elif self.stage == "actions":
            actor = self.actor()
            if actor:
                menu = {str(n): ("action", entry) for n, entry in enumerate(actor.get("menu_actions", []), 1)}
                for n, name in enumerate(("map", "party", "log", "rules", "help"), len(menu) + 1):
                    menu[str(n)] = ("read", name)
        elif self.stage in ("target", "move"):
            menu = {str(n): ("entry", entry) for n, entry in enumerate(self.entries, 1)}
        elif self.stage == "reaction":
            menu = {"1": ("reaction", "accept"), "2": ("reaction", "decline")}
        elif self.stage == "recovery":
            menu = {"9": ("resend", None)}
        client.menu = menu
        return menu

    def show(self):
        menu = self.build()
        client = self.client
        if self.stage != "actions":
            client.output(self.context())
        if self.stage == "home":
            for n, actor in enumerate(client.view.get("party", []), 1):
                status = actor.get("status") or ("可操作" if actor["id"] in client.view.get("active", []) else "暂不可操作，可查看技能")
                client.output("%s. %s %s  生命 %s/%s  [%s]" % (n, actor["id"], actor["name"], actor["hp"], actor["max_hp"], status))
            client.output("5 地图  6 队伍资源  7 详细日志  8 操作帮助")
            if "9" in menu:
                client.output("9 战败重试")
        elif self.stage == "settlement":
            client.output("战斗结果：胜利。本版的前哨战已完成，当前没有下一关。")
            summary = client.view.get("battle_summary", {})
            if summary:
                client.output("本场摘要：%s 回合；倒地 %s 次，死亡 %s；消耗法术位 %s、药水 %s；双方累计伤害 %s、治疗 %s。" % (client.view.get("round", "?"), summary.get("downed", 0), summary.get("deaths", 0), summary.get("slots", 0), summary.get("potions", 0), summary.get("damage", 0), summary.get("healing", 0)))
            remaining = client.view.get("short_rests", 0)
            client.output("1 全队短休（剩余 %s 次%s）" % (
                remaining, "，已用完" if remaining <= 0 else "；仅用于演示，无后续战斗"))
            client.output("2 查看全队资源  3 新开一场战斗（同阵容、新周目）")
            client.output("4 查看地图  5 详细日志  6 战利品说明")
            client.output("  ".join(n+" "+v+" 成长与装备" for n,(k,v) in menu.items() if k=="growth"))
            if "11" in menu: client.output("11 沿用当前成长与装备重新演练（全部资源刷新）")
            client.output((client.view.get("settlement") or {}).get("loot_description", "本场没有可拾取战利品，暂时无法搜刮遗体。"))
        elif self.stage == "growth":
            client.output(render_growth(self.actor()))
            for n,(_,entry) in menu.items(): client.output(n+'. '+entry[0])
            client.output('选择后先预览，c确认；0返回结算。非熟练护甲会阻止施法并使攻击劣势。')
        elif self.stage == "actions":
            client.output(render_menu_actions(client.view, self.selected, compact=self.compact))
            reads = {"map": "地图", "party": "队伍资源", "log": "详细日志", "rules": "技能与规则说明", "help": "操作帮助"}
            client.output("  ".join("%s %s" % (n, reads[value]) for n, (kind, value) in menu.items() if kind == "read"))
            if not self.actor().get("menu_actions"):
                client.output("服务端未提供中文行动菜单，请升级服务端；原快捷命令仍可使用。")
        elif self.stage in ("target", "move"):
            if self.stage == "move":
                client.output(render_map(client.view, self.selected, self.options.get("reachable", [])))
                client.output("常用目的地（路径成本由服务端计算）：")
            client.output(render_options(self.entries))
            if self.stage == "move":
                client.output("也可输入格坐标，如 G6；+ 表示本回合可沿真实路径到达。")
            elif self.action.get("target_mode") == "multi":
                client.output("输入多个编号，如 1,2,3（最多 %s 个）；单个编号也可以。" % self.options.get("max_targets", self.action.get("max_targets", 3)))
        elif self.stage == "reaction":
            client.output(client.view["pending"].get("description", "请选择反应。"))
            client.output("1 借机攻击（消耗 1 次反应）  2 放弃反应（不消耗）")
        elif self.stage == "recovery":
            client.output("9 恢复未确认请求；已保存原请求编号，请先恢复回执。")
        elif self.stage == "confirm":
            client.output("c 确认执行；数字不会执行行动。")
        client.output(("0 返回  " if self.stage in ("actions", "target", "move", "confirm") else "") + "q 退出（已执行动作自动保存）")
        self.revision = client.view.get("revision") if self.stage not in ("confirm", "target", "move") else self.revision

    def check_revision(self):
        expected = self.revision
        self.client.refresh(display=False)
        if expected != self.client.view.get("revision"):
            self.confirmation = None
            self.action = None
            self.stage = "actions" if self.selected else "home"
            self.show()
            raise ParseError("局势已变化，原列表或预览已过期；请重新选择。")

    def select_actor(self, actor_id):
        if not any(a["id"] == actor_id for a in self.client.view.get("party", [])):
            raise ParseError("没有队员 %s。" % actor_id)
        self.selected = actor_id
        self.compact = False
        self.action = None
        self.stage = "actions"
        self.client.output(render_map(self.client.view, self.selected))
        self.show()

    def select_action(self, entry):
        self.check_revision()
        if not entry.get("available"):
            raise ParseError("%s不可用：%s" % (entry["name"], entry.get("reason", "当前资源或阶段不满足")))
        self.action = deepcopy(entry)
        command = deepcopy(entry["command"])
        mode = entry.get("target_mode", "none")
        if mode == "none":
            self.preview(command, "actions")
            return
        result = self.client.transport.options(self.client.run_id, command)
        self.client.refresh(display=False)
        if result.get("revision") != self.client.view.get("revision"):
            self.stage = "actions"
            self.show()
            raise ParseError("获取列表期间局势已变化；请重新选择行动。")
        self.options = result
        self.entries = result.get("landmarks", []) if mode == "move" else result.get("entries", [])
        self.revision = result["revision"]
        self.stage = "move" if mode == "move" else "target"
        self.show()

    def preview(self, command, return_stage):
        number = self.client._preview(command, display=False)
        self.return_stage = return_stage
        self.stage = "confirm"
        self.confirmation = {"operation": "commands", "number": number}
        self.revision = self.client.previews[number]["revision"]
        self.client.output(self.context())
        self.client.output(render_preview(number, self.client.preview_result, self.revision, self.client.view))

    def select_entries(self, numbers):
        self.check_revision()
        if len(set(numbers)) != len(numbers):
            raise ParseError("目标编号不能重复。")
        selected = []
        for number in numbers:
            if not 1 <= number <= len(self.entries):
                raise ParseError("没有这个列表编号。")
            entry = self.entries[number - 1]
            if not entry.get("available"):
                raise ParseError("%s不可用：%s" % (entry["name"], entry.get("reason", "目标不合法")))
            selected.append(entry)
        mode = self.action.get("target_mode")
        if mode != "multi" and len(selected) != 1:
            raise ParseError("此行动只能选择一个条目。")
        if len(selected) > self.options.get("max_targets", self.action.get("max_targets", 3)):
            raise ParseError("选择的目标数量超过技能上限。")
        command = deepcopy(self.action["command"])
        if mode == "move":
            command["destination"] = selected[0]["destination"]
        elif mode == "multi":
            command["targets"] = [entry["target"] for entry in selected]
        else:
            command["target"] = selected[0]["target"]
        self.preview(command, self.stage)

    def back(self):
        if self.stage == "confirm":
            if self.confirmation and "number" in self.confirmation:
                self.client.previews.pop(self.confirmation["number"], None)
            self.stage = self.return_stage
            self.confirmation = None
            if self.stage in ("home", "settlement", "reaction", "actions"):
                self.action = None
        elif self.stage in ("target", "move"):
            self.stage = "actions"
            self.action = None
        elif self.stage == "growth":
            self.stage = "settlement"
        elif self.stage == "actions":
            self.stage = "home"
            self.selected = None
            self.action = None
        self.show()

    def after_write(self, request):
        command = request["payload"].get("command", {})
        self.confirmation = None
        self.action = None
        self.compact = True
        if request["operation"] == "create":
            self.selected = None
            self.entries = []
            self.options = {}
        if command.get("actor") and not self.selected:
            self.selected = command["actor"]
        active = self.client.view.get("active", [])
        if self.selected and self.selected not in active:
            ids = [a["id"] for a in self.client.view.get("party", [])]
            start = ids.index(self.selected) + 1 if self.selected in ids else 0
            ordered = ids[start:] + ids[:start]
            self.selected = next((aid for aid in ordered if aid in active), None)
            self.compact = False
        self.stage = "actions" if self.selected else "home"
        self.sync()

    def handle(self, line):
        """Return None for a legacy command, or a bool for handled input."""
        line = line.strip().lower()
        if line == "q":
            return self.client.handle("quit")
        if line == "0":
            self.back()
            return True
        if line == "c":
            if self.stage != "confirm" or not self.confirmation:
                raise ParseError("当前没有待确认预览；请先选择行动和目标。")
            confirmation = self.confirmation
            if confirmation["operation"] == "commands":
                self.client._do(confirmation["number"])
            elif confirmation["operation"] == "create":
                self.check_revision()
                self.client.create(confirmation["name"], confirmation["templates"])
            else:
                self.check_revision()
                self.client._write(confirmation["operation"], confirmation.get("extra"), revision=self.revision)
            return True
        if self.stage == "move" and re.fullmatch(r"[a-z]+[1-9][0-9]*", line):
            self.check_revision()
            self.preview({**deepcopy(self.action["command"]), "destination": line.upper()}, "move")
            return True
        if self.stage == "target" and re.fullmatch(r"[0-9]+(?:\s*[,， ]\s*[0-9]+)*", line):
            self.select_entries([int(n) for n in re.split(r"\s*[,， ]\s*", line)])
            return True
        if not line.isdigit():
            return None
        choice = self.build().get(line)
        if not choice:
            raise ParseError("没有这个列表编号；数字只选择当前列表。c 确认，0 返回，q 退出。")
        kind, value = choice
        if kind == "actor":
            self.check_revision()
            self.select_actor(value)
        elif kind == "growth":
            self.check_revision(); self.selected=value; self.stage='growth'; self.show()
        elif kind == "manage":
            self.check_revision(); self.action={'name':value[0]}; self.preview(value[1],'growth'); self.show()
        elif kind == "action":
            self.select_action(value)
        elif kind == "entry":
            self.select_entries([int(line)])
        elif kind == "read":
            self.client.refresh(display=False)
            if value == "map":
                self.client.output(render_map(self.client.view, self.selected))
            elif value == "party":
                self.client.output(render_party(self.client.view))
            elif value == "log":
                self.client._log(10)
            elif value == "rules":
                self.client.output(render_rules(self.client.view, self.selected))
            elif value == "loot":
                self.client.output((self.client.view.get("settlement") or {}).get("loot_description", "本场没有可拾取战利品，暂时无法搜刮遗体。"))
            else:
                from .cli import HELP
                self.client.output(HELP)
            self.show()
        elif kind == "resend":
            self.client._send_pending()
        elif kind == "reaction":
            self.check_revision()
            self.return_stage = "reaction"
            self.stage = "confirm"
            self.action = {"name": "借机攻击" if value == "accept" else "放弃反应"}
            self.confirmation = {"operation": "reactions", "extra": {"choice": value}}
            self.client.output(self.context())
            preview = self.client.view["pending"].get("preview")
            if value == "accept" and preview:
                self.client.output(render_preview("反应", preview, self.revision, self.client.view))
            else:
                self.client.output("影响：" + ("对离开触及的敌人进行一次武器攻击；消耗反应 1。" if value == "accept" else "不进行攻击，不消耗反应；继续敌方移动。"))
            self.show()
        elif kind == "practice":
            self.check_revision(); self.action={"name":"重新演练"}; self.preview({"type":"practice"},"settlement"); self.show()
        elif kind == "rest":
            self.check_revision()
            if self.client.view.get("short_rests", 0) <= 0:
                self.action = None
                raise ParseError("全队短休不可用：短休次数已用完。")
            self.action = {"name": "全队短休"}
            try:
                self.preview({"type": "rest", "kind": "short"}, "settlement")
            except Exception:
                self.action = None
                raise
        elif kind == "new_run":
            self.check_revision()
            self.return_stage = "settlement"
            self.stage = "confirm"
            self.action = {"name": "新开一场战斗"}
            self.confirmation = {"operation": "create", "name": self.client.view.get("name", "远征队"),
                                 "templates": [a["template"] for a in self.client.view.get("party", [])]}
            self.client.output("新建独立周目，使用相同的四人职业阵容，生命和资源从初始模板重新开始。")
            self.client.output("原周目已保留，可用 --run %s 恢复；新战斗开始后自动保存。" % self.client.run_id)
            self.show()
        elif kind == "retry":
            self.check_revision()
            self.return_stage = "home"
            self.stage = "confirm"
            self.action = {"name": "战败重试"}
            self.confirmation = {"operation": "retry"}
            self.client.output("将恢复本场战前生命、资源、布局与随机数状态。")
            self.show()
        return True
