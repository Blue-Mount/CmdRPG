"""Deterministic, command-driven combat. All writes operate on a copy."""

from copy import deepcopy
import secrets
import uuid

from .content import load_content
from . import dice, spatial
from .growth import GrowthMixin, DATA, SELF, FRIENDLY, PROF, proficiency, snapshot, recalculate, LEVEL_CAP, EQUIPMENT_SLOTS, CONTENT_VERSION, RULES_VERSION
from .advanced import AdvancedMixin
from .campaign import CampaignMixin, load_campaign, COMMANDS as CAMPAIGN_COMMANDS, CONTENT_VERSION as CAMPAIGN_VERSION
from .tactics import TacticsMixin, TACTICAL
from .equipment import bonus as equipment_bonus


class GameError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def modifier(score):
    return (score - 10) // 2


class Engine(AdvancedMixin, TacticsMixin, CampaignMixin, GrowthMixin):
    def __init__(self, content=None):
        self.content = content if content is not None else load_content()
        self.content = deepcopy(self.content)
        self.content["abilities"].update(deepcopy(DATA["abilities"]))
        self.content["items"].update(deepcopy(DATA["items"]))
        self.content["version"]=CONTENT_VERSION
        self.content["rules_version"]=RULES_VERSION
        self.campaign_content = load_campaign()

    def _check_version(self, state):
        if state.get('campaign') and state['campaign'].get('version') != self.campaign_content['version']:
            raise GameError('version_mismatch', '战役版本不匹配，请使用对应版本服务。')
        if state.get('campaign') and (type(state['campaign'].get('edition', 1)) is not int or state['campaign'].get('edition', 1) not in (1, 2)):
            raise GameError('version_mismatch', '战役路线版本不受支持。')
        if any(a.get("growth_version") not in (None,1) for a in state.get("actors",{}).values()):
            raise GameError("version_mismatch","成长存档版本不受支持，请使用对应版本服务。")
        if (state.get('content_version'),state.get('rules_version'),state.get('save_version'))==('0.1.0-slice','0.1.0-provisional',1):
            return  # Explicit legacy adapter: no mutation or implicit resource grants.
        previous = '0.4.0-m4' if state.get('campaign') else '0.3.0-m3'
        if (state.get('content_version'), state.get('rules_version'), state.get('save_version')) == (previous, '0.3.0-provisional', 1):
            return
        previous = '0.5.0-campaign' if state.get('campaign') else '0.5.0-m5'
        if (state.get('content_version'), state.get('rules_version'), state.get('save_version')) == (previous, '0.5.0-provisional', 1):
            return
        for field, content_field in (("content_version", "version"), ("rules_version", "rules_version"), ("save_version", "save_version")):
            expected = CAMPAIGN_VERSION if field == 'content_version' and state.get('campaign') else self.content[content_field]
            if state.get(field) != expected:
                raise GameError("version_mismatch", "存档版本与内容包不匹配，请使用对应版本服务或明确迁移。")

    def new_run(self, name="远征队", templates=None, seed=None, *, levels=None, loadouts=None, campaign=False, _carry=None):
        if type(campaign) is not bool:
            raise GameError('invalid_campaign', 'campaign 须为布尔值。')
        if campaign:
            if levels is not None or loadouts is not None:
                raise GameError('invalid_campaign', '正式战役从1级起步，不接受开发局配置。')
            return self._new_campaign(name, templates, seed)
        templates = ["fighter", "monk", "wizard", "cleric"] if templates is None else templates
        if not isinstance(name, str) or not name.strip() or len(name) > 40:
            raise GameError("invalid_party", "队伍名应为 1 至 40 个字符。")
        if not isinstance(templates, list) or len(templates) != 4 or any(not isinstance(t, str) or t not in self.content["classes"] for t in templates):
            raise GameError("invalid_party", "请选择恰好四个已实现职业：fighter、monk、wizard、cleric。")
        if seed is not None and (type(seed) is not int or not 0 <= seed <= dice.MASK):
            raise GameError("invalid_seed", "种子应为 0 至 2^64-1 的整数。")
        state = {
            "id": str(uuid.uuid4()), "name": name.strip(), "revision": 0,
            "content_version": self.content["version"], "rules_version": self.content["rules_version"],
            "save_version": self.content["save_version"], "phase": "combat", "outcome": None,
            "rng_state": secrets.randbits(64) if seed is None else seed,
            "round": 1, "actors": {}, "map": deepcopy(self.content["map"]),
            "cursor": 0, "active_group": [], "pending": None,
            "event_log": [], "event_seq": 0, "short_rests": 2,
            "encounter_name": self.content["encounter"]["name"],
        }
        for index, template in enumerate(templates):
            data = self.content["classes"][template]
            actor_id = f"P{index + 1}"
            state["actors"][actor_id] = {
                "id": actor_id, "name": data["name"], "template": template, "side": "party", "level": 1,
                "hp": data["hp"], "max_hp": data["hp"], "ac": data["ac"], "base_ac": data["ac"],
                "stats": deepcopy(data["stats"]), "speed": data["speed"], "position": self.content["encounter"]["party_spawns"][index],
                "weapon": data["weapon"], "attack_stat": data["attack_stat"], "spell_stat": data.get("spell_stat"),
                "save_proficiencies": list(data.get("save_proficiencies", [])),
                "abilities": list(data["abilities"]), "slots": deepcopy(data.get("slots", {})),
                "max_slots": deepcopy(data.get("slots", {})), "resources": {**data.get("resources", {}), "potions": 2},
                "max_resources": deepcopy(data.get("resources", {})), "conditions": [], "concentration": None,
                "effects": {}, "death_success": 0, "death_failure": 0, "temp_hp": 0,
                "budgets": {"action": 0, "bonus": 0, "reaction": 1, "movement": 0}, "ended": True, "attacked": False,
            }
        for entry in self.content["encounter"]["enemies"]:
            data = self.content["enemies"][entry["prototype"]]
            state["actors"][entry["id"]] = {
                "id": entry["id"], "name": data["name"], "template": entry["prototype"], "side": "enemy", "level": 1,
                "hp": data["hp"], "max_hp": data["hp"], "ac": data["ac"], "base_ac": data["ac"],
                "stats": deepcopy(data["stats"]), "speed": data["speed"], "position": entry["position"],
                "attack_stat": data.get("attack_stat", "dex" if data["role"] == "ranged" else "str"),
                "save_proficiencies": list(data.get("save_proficiencies", [])),
                "range": data["range"], "dice": data["dice"], "damage_type": data["damage_type"], "role": data["role"],
                "conditions": [], "concentration": None, "effects": {}, "temp_hp": 0,
                "budgets": {"action": 0, "bonus": 0, "reaction": 1, "movement": 0}, "ended": True, "attacked": False,
            }
        for entry in self.content['encounter']['enemies']:
            data = self.content['enemies'][entry['prototype']]
            for field in ('resistances', 'spell', 'boss'):
                if field in data:
                    state['actors'][entry['id']][field] = deepcopy(data[field])
        if levels is not None:
            if not isinstance(levels,list) or len(levels)!=4 or any(type(n)!=int or not 1<=n<=LEVEL_CAP for n in levels):
                raise GameError("invalid_level", "试玩等级须为四个1至8整数。")
        for i in range(4):
            snapshot(state["actors"][f"P{i+1}"], (levels or [1]*4)[i], self.content["items"])
        if loadouts:
            state['phase']='victory'
            for command in loadouts:
                if command.get('type') not in ('equip','prepare'): raise GameError('invalid_setup','战前配置只接受装备与准备。')
                state,_=self._management_execute(state,command)
            for actor in state['actors'].values():
                if actor['side'] == 'party' and 'ki' in actor['max_resources']:
                    actor['resources']['ki'] = actor['max_resources']['ki']
            state['phase']='combat'; state['revision']=0
        if _carry:
            state.update({key: deepcopy(value) for key, value in _carry.items() if key != 'party'})
            state['content_version'] = CAMPAIGN_VERSION
            for i in range(1, 5):
                aid = f'P{i}'
                if 'party' in _carry:
                    position = state['actors'][aid]['position']
                    state['actors'][aid] = deepcopy(_carry['party'][aid])
                    state['actors'][aid].update(position=position, ended=True, attacked=False,
                        budgets={'action': 0, 'bonus': 0, 'reaction': 1, 'movement': 0})
                    for field in ('last_turn_round', 'attacks_remaining'):
                        state['actors'][aid].pop(field, None)
                else:
                    actor = state['actors'][aid]
                    actor['inventory'] = list(dict.fromkeys(v for v in actor['equipment'].values() if v))
        initiatives = {actor_id: dice.die(state, 4) + modifier(a["stats"]["dex"]) for actor_id, a in state["actors"].items()}
        # Stable ties: party first, then higher Dexterity, then actor ID.
        state["turn_order"] = sorted(state["actors"], key=lambda aid: (-initiatives[aid], state["actors"][aid]["side"] != "party", -state["actors"][aid]["stats"]["dex"], aid))
        state["initiative"] = initiatives
        state["battle_snapshot"] = deepcopy(state)
        self._emit(state, "encounter", f"{state['encounter_name']}：{self.content['encounter']['description']}")
        self._advance(state)
        return state

    def _emit(self, state, kind, text, **details):
        # Area effects may resolve unseen enemies. Do not publish their rolls,
        # HP, identity, or damage totals in the player's event stream.
        subject=state['actors'].get(text.split(' ',1)[0])
        if subject and subject['side']=='enemy' and not self._visible(state,subject): return
        for observed in state["actors"].values():
            if observed["side"] == "enemy" and self._visible(state, observed):
                state.setdefault("last_seen", {})[observed["id"]] = {"id": observed["id"], "name": observed["name"], "position": observed["position"], "round": state["round"]}
        summary = state.setdefault("battle_summary", {"damage":0, "healing":0, "downed":0, "deaths":0, "slots":0, "potions":0})
        if kind == "damage":
            summary["damage"] += details.get("applied_damage", 0)
        elif kind == "heal":
            summary["healing"] += details.get("actual_healing", 0)
        elif kind in ("downed", "death"):
            summary["downed" if kind == "downed" else "deaths"] += 1
        elif kind == "ability" and details.get("slot", 0):
            summary["slots"] += 1
        state["event_seq"] += 1
        text = text.replace("AC ", "防御 ").replace("DC ", "难度 ").replace("d20", "二十面骰")
        event = {"seq": state["event_seq"], "kind": kind, "text": text, "details": details}
        state["event_log"].append(event)
        state["event_log"] = state["event_log"][-100:]

    def _visible(self, state, actor):
        if actor["side"] == "party":
            return True
        for observer in state["actors"].values():
            if observer["side"] != "party" or observer["hp"] <= 0:
                continue
            dist = spatial.distance(state["map"], observer["position"], actor["position"])
            if dist <= (3 if actor.get("hidden") else 18) and spatial.line_of_sight(state["map"], observer["position"], actor["position"]):
                return True
        return False

    def _alive(self, actor):
        return actor["hp"] > 0 and "dead" not in actor["conditions"]

    def _active(self, state):
        return [aid for aid in (state["active_group"] if state["phase"] == "combat" else []) if state["actors"][aid]["side"] == "party" and self._alive(state["actors"][aid]) and "stunned" not in state["actors"][aid]["conditions"] and not state["actors"][aid]["ended"]]

    def view(self, state):
        self._check_version(state)
        party, enemies = [], []
        common = ("id", "name", "template", "side", "level", "hp", "max_hp", "ac", "position", "speed", "stats", "conditions", "ended", "budgets")
        for actor in state["actors"].values():
            if not self._visible(state, actor):
                continue
            result = {key: deepcopy(actor[key]) for key in common}
            if actor["side"] == "party":
                result.update({key: deepcopy(actor[key]) for key in ("resources", "slots", "concentration", "death_success", "death_failure", "temp_hp", "effects")})
                conditions = actor["conditions"]
                result["status"] = ("死亡" if "dead" in conditions else
                    "倒地·伤势稳定" if "stable" in conditions else
                    f"倒地（死亡豁免 成功 {actor['death_success']} / 失败 {actor['death_failure']}）" if "downed" in conditions else
                    "战斗已结束" if state["phase"] != "combat" else
                    "可操作" if actor["id"] in self._active(state) else
                    "已结束操作" if actor.get("last_turn_round") == state["round"] else "未轮到")
                if result["concentration"]:
                    result["concentration"]["name"] = self.content["abilities"][result["concentration"]["ability"]]["name"]
                for effect_id, effect in result["effects"].items():
                    effect["name"] = self.content["abilities"].get(effect_id, {}).get("name", effect_id)
                    effect["durations"] = {source: state["actors"][source]["concentration"]["remaining"]
                        for source in effect.get("sources", [effect['source']] if 'source' in effect else []) if state["actors"].get(source, {}).get("concentration")}
                for key in ("equipment", "inventory", "prepared", "known_spells", "always_prepared", "proficiency", "extra_attack", "max_slots", "max_resources"):
                    if key in actor: result[key]=deepcopy(actor[key])
                if 'equipment' in result:
                    result['equipment'] = dict.fromkeys(EQUIPMENT_SLOTS) | result['equipment']
                result['passives'] = deepcopy(actor.get('passives', []))
                result['growth_history'] = deepcopy(actor.get('growth_history', []))
                result['last_growth'] = deepcopy(actor.get('last_growth'))
                from .equipment import stats
                combat = stats(actor, self.content['items'])
                combat['proficiencies'] = sorted(PROF[actor['template']])
                combat['attacks_per_action'] = 1 + actor.get('extra_attack', 0)
                combat['attacks_remaining'] = (actor['budgets']['action'] * combat['attacks_per_action'] + actor.get('extra_attacks', 0)) if actor['id'] in self._active(state) and self._alive(actor) else 0
                combat['unarmed_attack_bonus'] = proficiency(actor) + max(modifier(actor['stats']['str']), modifier(actor['stats']['dex']))
                result['combat_stats'] = combat
                result['last_concentration_end'] = deepcopy(actor.get('last_concentration_end'))
                result["equipment_names"] = {k:self.content["items"].get(v,{}).get("name","无") for k,v in actor.get("equipment",{}).items()}
                result["inventory_items"] = [{"id":k,"name":self.content["items"][k]["name"],"slot":self.content["items"][k]["slot"]} for k in actor.get("inventory",[]) if self.content["items"][k]["kind"]!="potion"]
                result["abilities"] = self.actions(state, actor["id"])
                result["menu_actions"] = self.menu_actions(state, actor["id"])
                party.append(result)
            else:
                result["range"] = actor.get("range", 1.5)
                for field in ('resistances', 'spell', 'save_proficiencies'):
                    if field in actor: result[field] = deepcopy(actor[field])
                if actor.get('boss'):
                    result['boss_phase'] = actor.get('boss_phase', 1)
                    if result['boss_phase'] == 2:
                        result['boss_rule'] = {k: actor['boss'][k] for k in ('radius', 'dice', 'dc')}
                enemies.append(result)
        pending = None
        if state["pending"]:
            raw = state["pending"]
            reactor, mover = state["actors"][raw["reactor"]], state["actors"][raw["mover"]]
            adv, dis, height = self._attack_context(state, reactor, mover)
            bonus = self._weapon_proficiency(reactor) + modifier(reactor["stats"][reactor["attack_stat"]]) + height + equipment_bonus(reactor, self.content["items"], "attack_bonus")
            description = f"{mover['id']} {mover['name']} 即将离开 {reactor['id']} {reactor['name']} 的近战触及，是否借机攻击？消耗 1 次反应。"
            pending = {"kind": raw["kind"], "reactor": raw["reactor"], "mover": raw["mover"],
                       "description": description, "choices": ["accept", "decline"],
                       "preview": {"revision": state["revision"], "description": description,
                                   "cost": {"reaction": 1}, "targets": [mover["id"]], "warnings": [],
                                   "hit_probability": dice.hit_probability(bonus, mover["ac"], adv, dis, self._blessed(reactor))}}
            pending["preview"]["attack_roll"] = self._attack_roll_info(state, reactor, mover)
            if (raw.get("after") or {}).get("type") == "enemy_turn":
                pending["preview"]["warnings"].append("反应结束后，敌人若仍能行动，会继续剩余移动与攻击。")
        return {
            "id": state["id"], "name": state["name"], "revision": state["revision"], "phase": state["phase"],
            "initiative_order": [{"id": aid, "current": aid in state["active_group"] and state["phase"] == "combat"}
                for aid in state["turn_order"] if state["actors"][aid]["side"] == "party" or self._visible(state, state["actors"][aid]) or aid in state.get("last_seen", {})],
            "last_seen": [deepcopy(entry) for aid, entry in state.get("last_seen", {}).items() if not self._visible(state, state["actors"][aid])],
            "round": state["round"], "active": self._active(state), "party": party, "enemies": enemies,
            "map": deepcopy(state["map"]), "pending": pending, "recent_events": deepcopy(state["event_log"][-6:]),
            "battle_summary": deepcopy(state.get("battle_summary", {})),
            "campaign": self._campaign_view(state),
            "development": state.get("development",False), "outcome": state["outcome"], "content_version": state["content_version"], "rules_version": state["rules_version"],
            "encounter_name": state["encounter_name"], "short_rests": state["short_rests"],
            "settlement": self._campaign_settlement(state) if state.get('campaign') else {"completed": True, "next_encounter": None, "loot_available": False,
                           "loot_description": "本场没有配置可拾取战利品；目前无法搜刮遗体。"}
            if state["phase"] == "victory" else None,
        }

    def actions(self, state, actor_id):
        actor = state["actors"].get(actor_id)
        if not actor or actor["side"] != "party":
            raise GameError("unknown_actor", "未找到队员。")
        entries = []
        for ability_id in actor["abilities"]:
            ability = self.content["abilities"][ability_id]
            reason = ""
            if state["pending"]:
                reason = "请先选择待处理反应"
            elif state["phase"] != "combat" or actor_id not in self._active(state):
                reason = "当前不可操作此队员"
            elif actor["budgets"].get(ability["cost"],1) <= 0 and not (ability_id == "stunning_strike" and actor.get("extra_attacks", 0)):
                reason = {"action": "动作", "bonus": "附赠动作", "reaction": "反应"}.get(ability["cost"], ability["cost"]) + "资源不足"
            elif ability["level"] and not any(int(k) >= ability["level"] and v > 0 for k, v in actor["slots"].items()):
                reason = "法术位不足"
            elif ability_id == "second_wind" and not actor["resources"].get("second_wind", 0):
                reason = "短休资源已用完"
            elif ability_id == "unarmed_strike" and not actor["attacked"]:
                reason = "需要先攻击：本回合使用攻击动作后解锁"
            if not reason and ability_id in ("flurry","patient_defence","unarmed_strike") and (actor.get("equipment",{}).get("armor") or actor.get("equipment",{}).get("off_hand")): reason="武艺需要无甲无盾"
            if not reason and ability.get("resource") and not actor["resources"].get(ability["resource"],0): reason="职业资源不足"
            if not reason and ability["level"] and actor.get("growth_version") and ability_id not in actor.get("prepared",[]): reason="法术尚未准备"
            if not reason and (ability["level"] or ability_id in ("fire_bolt","sacred_flame")):
                try: self._casting_equipment(actor)
                except GameError as exc: reason=exc.message
            entries.append({"id": ability_id, **deepcopy(ability), "available": not reason, "reason": reason})
        return entries

    def _default_slot(self, actor, level):
        """Menus choose the cheapest slot; explicit command slots still work."""
        return min((int(slot) for slot, count in actor["slots"].items()
                    if count > 0 and int(slot) >= level), default=None)

    def _skill_summary(self, actor, ability_id, slot=None):
        """Brief gameplay effects; detailed content notes remain in actions."""
        if ability_id in DATA["abilities"]: return DATA["abilities"][ability_id]["description"]
        slot = slot or self.content["abilities"][ability_id]["level"]
        healing_bonus = modifier(actor["stats"][actor["spell_stat"]]) + (2 + slot if actor["template"] == "cleric" else 0) if actor.get("spell_stat") else 0
        healing_bonus += equipment_bonus(actor, self.content['items'], 'healing_bonus')
        descriptions = {
            "second_wind": f"自身恢复 1d10+{actor['level']} 生命；短休恢复使用次数。",
            "unarmed_strike": f"先使用攻击动作，再附赠攻击相邻敌人；伤害 1d{6 if actor['level']>=3 else 4}{modifier(actor['stats'][actor['attack_stat']]):+d}。",
            "fire_bolt": f"攻击18米内可见敌人，命中造成 {2 if actor['level']>=5 else 1}d10 火焰伤害。",
            "magic_missile": f"对 18 米内可见敌人发射 {3 + slot - 1} 枚自动命中飞弹，可逐枚分配，每枚 1d4+1 力场伤害。",
            "mage_armor": "无甲队友防御至少为 13+敏捷修正，持续本场战斗。",
            "sacred_flame": f"18米内可见敌人作敏捷豁免；失败受到 {2 if actor['level']>=5 else 1}d8 光耀伤害。",
            "healing_word": f"恢复 18 米内可见队友 {slot}d4{healing_bonus:+d} 生命，可唤醒倒地队友。",
            "cure_wounds": f"恢复相邻队友 {slot}d8{healing_bonus:+d} 生命，可唤醒倒地队友。",
            "bless": f"至多 {3 + slot - 1} 名队友攻击与豁免加 1d4；需要专注，持续至多 10 回合。",
        }
        return descriptions[ability_id]

    def menu_actions(self, state, actor_id):
        """Player-facing action metadata, including actions that cannot be used.

        Availability here describes the actor's resources. Target restrictions
        are checked by options/preview so players can inspect blocked targets.
        """
        actor = state["actors"].get(actor_id)
        if not actor or actor["side"] != "party":
            raise GameError("unknown_actor", "未找到队员。")
        entries = []
        ordinary = [
            ("move", "移动", "沿可通行路径移动；先查看可达范围与目的地。", {}, "move"),
            ("attack", "普通攻击", "使用当前武器攻击相邻敌人。", {"action": 1}, "enemy"),
            ("dash", "疾走", f"额外获得 {actor['speed']:g} 米移动。", {"action": 1}, "none"),
            ("disengage", "撤离", "本回合移动不触发借机攻击。", {"action": 1}, "none"),
            ("dodge", "闪避", "直到下回合，攻击自己具有劣势。", {"action": 1}, "none"),
            ("help", "救助", "救助相邻倒地队员，恢复 1 生命。", {"action": 1}, "party"),
            ("potion", "饮用治疗药水", "恢复自身 2d4+2 生命。", {"bonus": 1, "potions": 1}, "none"),
            ("end", "结束角色操作", "结束此角色操作，保留反应；切换到下一位可操作队员。", {}, "none"),
        ]
        for kind, name, description, cost, mode in ordinary:
            if kind=='attack' and actor.get('extra_attacks',0): cost={}
            reason = ""
            if state["pending"]:
                reason = "请先选择待处理反应"
            elif state["phase"] != "combat" or actor_id not in self._active(state):
                reason = "当前不可操作此队员"
            elif kind == "move" and actor["budgets"]["movement"] <= 0:
                reason = "移动距离已用完，可先选择疾走"
            elif cost.get("action") and actor["budgets"]["action"] <= 0:
                reason = "动作已用完"
            elif cost.get("bonus") and actor["budgets"]["bonus"] <= 0:
                reason = "附赠动作已用完"
            elif kind == "potion" and actor["resources"].get("potions", 0) <= 0:
                reason = "治疗药水已用完"
            entries.append({"name": name, "description": description,
                            "command": {"type": kind, "actor": actor_id}, "cost": cost,
                            "available": not reason, "reason": reason, "target_mode": mode,
                            "remaining": {key: actor["budgets"].get(key, actor["resources"].get(key, 0))
                                          for key in cost}})
        for ability in self.actions(state, actor_id):
            ability_id = ability["id"]
            mode = ("none" if ability_id == "second_wind" or ability_id in SELF else
                    "allocation" if ability_id in ("magic_missile", "scorching_ray") else
                    "ground" if ability_id == "fireball" else
                    "multi" if ability_id == "bless" else
                    "party" if ability_id in ("healing_word", "cure_wounds", "mage_armor") or ability_id in FRIENDLY else "enemy")
            kind = "ability" if ability_id in ("second_wind", "unarmed_strike") or (ability_id in DATA["abilities"] and not ability["level"]) else "cast"
            command = {"type": kind, "actor": actor_id, "ability": ability_id}
            cost = {} if ability["cost"]=="free" else {ability["cost"]: 1}
            if ability_id == "stunning_strike" and actor.get("extra_attacks", 0): cost = {}
            remaining = {} if ability["cost"]=="free" else {ability["cost"]: actor["budgets"][ability["cost"]]}
            slot = None
            if ability["level"]:
                slot = self._default_slot(actor, ability["level"])
                display_slot = slot or ability["level"]
                cost[f"slot_{display_slot}"] = 1
                remaining[f"slot_{display_slot}"] = actor["slots"].get(str(display_slot), 0)
                if slot is not None:
                    command["slot"] = slot
            if ability.get("resource"):
                cost[ability["resource"]]=1
                remaining[ability["resource"]]=actor["resources"].get(ability["resource"],0)
            if ability_id == "second_wind":
                cost["second_wind"] = 1
                remaining["second_wind"] = actor["resources"].get("second_wind", 0)
            entry = {"name": ability["name"], "description": self._skill_summary(actor, ability_id, slot),
                     "command": command, "cost": cost, "remaining": remaining,
                     "available": ability["available"], "reason": ability["reason"],
                     "target_mode": mode}
            if mode == "multi":
                entry["max_targets"] = 3 + (slot or ability["level"]) - 1
            if mode == "allocation":
                entry["max_targets"] = (slot or ability["level"]) + (2 if ability_id == "magic_missile" else 1)
            entries.append(entry)
        return entries

    def options(self, state, command):
        """Pure menu discovery using the same geometry and legal previews as execution."""
        self._check_version(state)
        if not isinstance(command, dict) or not isinstance(command.get("type"), str):
            raise GameError("invalid_command", "菜单请求需要 type 字段。")
        kind = command["type"]
        keys = {"attack": {"type", "actor"}, "help": {"type", "actor"},
                "move": {"type", "actor"}, "cast": {"type", "actor", "ability", "slot"},
                "ability": {"type", "actor", "ability"}}
        if kind not in keys or set(command) - keys[kind]:
            raise GameError("invalid_command", "菜单请求包含不支持的行动或参数。")
        actor_id = command.get("actor")
        actor = state["actors"].get(actor_id) if isinstance(actor_id, str) else None
        if not actor or actor["side"] != "party":
            raise GameError("unknown_actor", "未找到队员。")
        base = deepcopy(command)
        response = {"revision": state["revision"], "command": base, "entries": []}
        if kind == "move":
            return self._move_options(state, actor, base, response)
        side, multi = ("enemy" if kind == "attack" else "party"), False
        if kind in ("cast", "ability"):
            ability_id = command.get("ability")
            if not isinstance(ability_id, str) or ability_id not in actor["abilities"]:
                raise GameError("unknown_ability", "此队员未拥有该能力。")
            ability = self.content["abilities"][ability_id]
            if ability["level"] and "slot" not in base:
                slot = self._default_slot(actor, ability["level"])
                if slot is not None:
                    base["slot"] = slot
            elif "slot" in base and (not ability["level"] or type(base["slot"]) is not int):
                raise GameError("invalid_slot", "法术位环阶无效。")
            if ability_id == "second_wind" or ability_id in SELF:
                response["preview"] = self.preview(state, base)
                return response
            multi = ability_id == "bless"
            side = "party" if ability_id in ("bless", "healing_word", "cure_wounds", "mage_armor") or ability_id in FRIENDLY else "enemy"
            if "slot" in base and (type(base["slot"]) is not int or base["slot"] < ability["level"] or actor["slots"].get(str(base["slot"]), 0) <= 0):
                raise GameError("invalid_slot", "没有指定环阶的合法法术位。")
            if multi:
                response["max_targets"] = 3 + base.get("slot", ability["level"]) - 1
            if ability_id in ("magic_missile", "scorching_ray"):
                response["max_targets"] = base.get("slot", ability["level"]) + (2 if ability_id == "magic_missile" else 1)
            if ability_id == "fireball":
                response["ground_targeting"] = True
        for target in state["actors"].values():
            if (target["side"] != side or not self._visible(state, target)
                    or (side == "enemy" and "dead" in target["conditions"])):
                continue
            candidate = {**base, "targets": [target["id"]]} if multi else {**base, "target": target["id"]}
            entry = {"target": target["id"], "id": target["id"], "name": target["name"],
                     "hp": target["hp"], "max_hp": target["max_hp"], "position": target["position"],
                     "distance": spatial.distance(state["map"], actor["position"], target["position"]),
                     "distance_description": self._distance_description(state, actor, target),
                     "available": False, "reason": ""}
            try:
                checked = self.preview(state, candidate)
                entry.update(available=True, preview=checked)
                for field in ("hit_probability", "failure_probability", "save_dc"):
                    if field in checked:
                        entry[field] = checked[field]
                if kind in ("cast", "ability") and base["ability"] == "magic_missile":
                    entry["hit_probability"] = 1.0
            except GameError as exc:
                entry.update(reason=exc.message, reason_code=exc.code)
            response["entries"].append(entry)
        return response

    def _move_options(self, state, actor, command, response):
        board = state["map"]
        occupied = {a["position"] for a in state["actors"].values()
                    if a["id"] != actor["id"] and "dead" not in a["conditions"] and self._visible(state, a)}
        landmarks = {landmark["position"]: landmark for landmark in board.get("landmarks", [])}
        # The door's two sides are useful tactical destinations in this slice.
        door = next((item for item in board.get("landmarks", []) if "门洞" in item["name"]), None)
        if door:
            x, y = spatial.xy(door["position"])
            for dx, name, description in ((-1, "门洞西侧", "靠近中央门洞，可沿门洞向东建立射线。"),
                                           (1, "门洞东侧", "穿过断墙，可接近东侧守军；注意敌人触及。")):
                cell = spatial.label(x + dx, y) if x + dx >= 0 else ""
                if spatial.walkable(board, cell):
                    landmarks.setdefault(cell, {"position": cell, "name": name, "description": description})
        response.update(reachable=[], landmarks=[])
        routes = spatial.paths(board, actor["position"], occupied)
        for y in range(board["height"]):
            for x in range(board["width"]):
                cell = spatial.label(x, y)
                if not spatial.walkable(board, cell):
                    continue
                landmark = landmarks.get(cell, {})
                route = routes.get(cell)
                entry = {"destination": cell, "name": landmark.get("name", cell),
                         "description": landmark.get("description", ""), "cost": route[1] if route else None,
                         "path": route[0] if route else [], "available": False, "reason": ""}
                try:
                    checked = self.preview(state, {**command, "destination": cell}, _routes=routes)
                    entry.update(available=True, preview=checked)
                    response["reachable"].append(cell)
                except GameError as exc:
                    entry.update(reason=exc.message, reason_code=exc.code)
                response["entries"].append(entry)
                if landmark:
                    response["landmarks"].append(deepcopy(entry))
        response["landmarks"].sort(key=lambda entry: (
            not entry["available"], entry["cost"] is None,
            entry["cost"] if entry["cost"] is not None else 0, entry["destination"]))
        return response

    def _actor(self, state, actor_id):
        if not isinstance(actor_id, str) or actor_id not in self._active(state):
            raise GameError("inactive_actor", "当前无法操作该队员；请查看 active 或处理待选反应。")
        return state["actors"][actor_id]

    def _target(self, state, target_id, side=None):
        target = state["actors"].get(target_id) if isinstance(target_id, str) else None
        if not target or not self._visible(state, target) or (side and target["side"] != side):
            raise GameError("invalid_target", "目标不是当前可见且合法的对象。")
        if "dead" in target["conditions"]:
            raise GameError("invalid_target", "目标已经死亡，无法攻击、治疗或救助；短休也不会复活。")
        return target

    def _distance_description(self, state, actor, target):
        """Explain the same three-dimensional straight-line range we resolve."""
        board = state["map"]
        ax, ay = spatial.xy(actor["position"])
        tx, ty = spatial.xy(target["position"])
        offsets = []
        if tx != ax:
            offsets.append(f"向{'东' if tx > ax else '西'} {abs(tx - ax)} 格")
        if ty != ay:
            offsets.append(f"向{'南' if ty > ay else '北'} {abs(ty - ay)} 格")
        horizontal = "、".join(offsets) if offsets else "同一格"
        dz = board.get("heights", {}).get(target["position"], 0) - board.get("heights", {}).get(actor["position"], 0)
        height = f"目标{'高' if dz > 0 else '低'} {abs(dz):g} 米" if dz else "同一高度"
        return f"{horizontal}，{height}；按直线与高差计算，每格 {board['cell_size']:g} 米。"

    def _in_range(self, state, actor, target, distance):
        actual = (spatial.reach_distance if distance <= 1.5 else spatial.distance)(state["map"], actor["position"], target["position"])
        if actual > distance + 1e-8:
            reason = f"目标距离 {actual:.2f} 米，超出 {distance:g} 米射程。{self._distance_description(state, actor, target)}"
            if distance <= state["map"]["cell_size"]:
                reason += "近身范围包含高差，高差可能使相邻格超出触及；同高度斜角相邻可触及。"
            raise GameError("out_of_range", reason)
        if not spatial.line_of_sight(state["map"], actor["position"], target["position"]):
            raise GameError("blocked_sight", "目标被墙体或高度遮挡。")

    def _need(self, actor, budget):
        if actor["budgets"][budget] <= 0:
            label = {"action": "动作", "bonus": "附赠动作", "reaction": "反应", "movement": "移动"}.get(budget, budget)
            raise GameError("insufficient_resource", f"{label}资源不足。")

    def _attack_context(self, state, attacker, target, ranged=False):
        info = self._attack_roll_info(state, attacker, target, ranged)
        return info["advantage"], info["disadvantage"], info["height_bonus"]

    def _attack_roll_info(self, state, attacker, target, ranged=False):
        distance = spatial.distance(state["map"], attacker["position"], target["position"])
        advantages, disadvantages = [], []
        if "stunned" in target["conditions"]:
            advantages.append("目标被震慑")
        if "downed" in target["conditions"]:
            advantages.append("目标倒地")
        if "prone" in target["conditions"]:
            (advantages if distance <= 1.5 else disadvantages).append("近处目标伏地" if distance <= 1.5 else "远处目标伏地")
        if "dodge" in target["conditions"] and "stunned" not in target["conditions"]:
            disadvantages.append("目标正在闪避")
        if "prone" in attacker["conditions"]:
            disadvantages.append("攻击者伏地")
        eq=attacker.get('equipment',{})
        if attacker['side']=='party':
            if any(self.content['items'].get(eq.get(k),{}).get('category') not in PROF[attacker['template']] for k in ('armor','off_hand') if eq.get(k)):
                disadvantages.append("护甲或盾牌未熟练")
        if ranged:
            if any(self._alive(a) and "stunned" not in a["conditions"] and a["side"] != attacker["side"] and (attacker["side"] == "enemy" or self._visible(state, a)) and spatial.reach_distance(state["map"], attacker["position"], a["position"]) <= 1.5 and spatial.line_of_sight(state["map"], attacker["position"], a["position"]) for a in state["actors"].values()):
                disadvantages.append("远程攻击时有敌人贴身")
        height_bonus = 0
        if ranged:
            delta = state["map"].get("heights", {}).get(attacker["position"], 0) - state["map"].get("heights", {}).get(target["position"], 0)
            height_bonus = 2 if delta >= 2.5 else -2 if delta <= -2.5 else 0
        return dict(advantage=bool(advantages), disadvantage=bool(disadvantages),
                    advantage_reasons=advantages, disadvantage_reasons=disadvantages, height_bonus=height_bonus)

    def _blessed(self, actor):
        return "bless" in actor["effects"]

    def preview(self, state, command, *, _routes=None):
        self._check_version(state)
        if not isinstance(command, dict) or not isinstance(command.get("type"), str):
            raise GameError("invalid_command", "命令需要 type 字段。")
        kind = command["type"]
        if kind in ("cast", "ability") and isinstance(command.get("ability"), str) and command["ability"] in TACTICAL:
            return self._tactical_preview(state, command)
        if kind in CAMPAIGN_COMMANDS:
            return self._campaign_preview(state, command)
        if kind=="practice": return self._practice_preview(state,command)
        if kind in ("levelup","equip","prepare","recover"):
            return self._management_preview(state,command)
        if isinstance(command.get("ability"),str) and command.get("ability") in DATA["abilities"] and kind in ("cast","ability"):
            if state["pending"]: raise GameError("reaction_pending","请先处理待选反应。")
            if state["phase"]!="combat": raise GameError("not_in_combat","战斗已结束。")
            return self._new_ability_preview(state,command)
        keys = {
            "attack": {"type", "actor", "target"}, "move": {"type", "actor", "destination"},
            "cast": {"type", "actor", "ability", "target", "targets", "slot"}, "ability": {"type", "actor", "ability", "target"},
            "end": {"type", "actor"}, "dash": {"type", "actor"}, "disengage": {"type", "actor"},
            "dodge": {"type", "actor"}, "help": {"type", "actor", "target"}, "potion": {"type", "actor"}, "rest": {"type", "kind"},
        }
        if kind not in keys or set(command) - keys[kind]:
            raise GameError("invalid_command", "不支持的命令或参数。请使用 help 查看已实现操作。")
        if state["pending"]:
            raise GameError("reaction_pending", "请先处理待选反应，其他行动暂时暂停。")
        result = {"command": deepcopy(command), "revision": state["revision"], "cost": {}, "description": "", "warnings": [], "targets": []}
        if kind == "rest":
            if command.get("kind") != "short":
                raise GameError("invalid_rest", "目前仅支持全队短休。")
            if state["phase"] != "victory":
                raise GameError("invalid_rest", "全队短休不可用：需要先取得战斗胜利。")
            if state["short_rests"] <= 0:
                raise GameError("invalid_rest", "全队短休不可用：本周目的两次短休已用完。")
            result["description"] = "全队短休：所有未死亡队员恢复一半最大生命（向上取整，最多到生命上限）与短休资源；不恢复法术位、药水，也不会复活已死亡队员。"
            if not state.get('campaign'):
                result["warnings"].append("本切片没有后续战斗，短休仅用于演示；仍会消耗次数。")
            result["cost"] = {"short_rests": 1}
            result["rest_effects"] = []
            for member in state["actors"].values():
                if member["side"] != "party" or "dead" in member["conditions"]:
                    continue
                after = min(member["max_hp"], member["hp"] + (member["max_hp"] + 1) // 2)
                restored = {resource: maximum - member["resources"].get(resource, 0)
                            for resource, maximum in member["max_resources"].items()
                            if resource != "arcane_recovery" and maximum > member["resources"].get(resource, 0)}
                result["targets"].append(member["id"])
                result["rest_effects"].append({"actor": member["id"], "name": member["name"],
                                               "hp_before": member["hp"], "hp_after": after,
                                               "max_hp": member["max_hp"], "healing": after - member["hp"],
                                               "resources_restore": restored})
            return result
        if state["phase"] != "combat":
            raise GameError("not_in_combat", "战斗已结束；战败可 retry，另开周目使用 --new。")
        actor = self._actor(state, command.get("actor"))
        if kind == "move":
            destination = command.get("destination")
            if not isinstance(destination, str) or not spatial.walkable(state["map"], destination):
                raise GameError("invalid_position", "目标位置不存在或不可通行。")
            if destination == actor["position"]:
                raise GameError("invalid_position", "已经位于目标位置。")
            occupant = next((a for a in state["actors"].values()
                             if a["id"] != actor["id"] and a["position"] == destination
                             and "dead" not in a["conditions"] and self._visible(state, a)), None)
            if occupant:
                raise GameError("occupied_position", f"目的地被 {occupant['id']} {occupant['name']} 占用。")
            occupied = {a["position"] for a in state["actors"].values() if a["id"] != actor["id"] and "dead" not in a["conditions"] and self._visible(state, a)}
            route = _routes.get(destination) if _routes is not None else spatial.path(state["map"], actor["position"], destination, occupied)
            if route is None:
                raise GameError("unreachable", "目标无法沿已知合法路径到达。")
            steps, cost = route
            if cost > actor["budgets"]["movement"]:
                raise GameError("insufficient_movement", f"路径需要 {cost:g} 米，剩余 {actor['budgets']['movement']:g} 米。")
            result.update(path=steps, description=f"沿 {' → '.join(steps)} 移动 {cost:g} 米。", cost={"movement": cost})
            hazards = [{"position": cell, **deepcopy(state["map"]["hazards"][cell])}
                       for cell in steps[1:] if cell in state["map"].get("hazards", {})]
            if hazards:
                result["hazards"] = hazards
                for hazard in hazards:
                    result["warnings"].append(f"{hazard['position']}：{hazard['description']} 每次进入受到 {hazard['damage']:g} 点火焰伤害。")
                result["warnings"].append(f"若完整走完，已知危险地面共造成 {sum(h['damage'] for h in hazards):g} 点伤害；中断时只结算已进入格。")
            threats = self._move_reactors(state, actor, steps)
            if threats:
                result["opportunity_attacks"] = threats
                names = "、".join(f"{a['id']} {a['name']}" for a in threats)
                result["warnings"].append(f"离开 {names} 的近战触及时可能遭受借机攻击，移动可能被中断。")
            return result
        if kind == "end":
            result["description"] = f"结束 {actor['id']} 的操作，保留剩余反应用于敌方行动。"
            return result
        if kind == "attack" and actor.get("extra_attacks",0)>0:
            result["cost"]={}
        elif kind in ("dash", "disengage", "dodge", "help", "attack"):
            self._need(actor, "action")
            result["cost"] = {"action": 1}
        if kind == "attack":
            target = self._target(state, command.get("target"), "enemy")
            self._in_range(state, actor, target, 1.5)
            adv, dis, height = self._attack_context(state, actor, target)
            bonus = self._weapon_proficiency(actor) + modifier(actor["stats"][actor["attack_stat"]]) + height + equipment_bonus(actor, self.content["items"], "attack_bonus")
            weapon = deepcopy(self.content["items"][actor["weapon"]])
            if actor["weapon"]=="monk_fists": weapon["dice"]=self._unarmed_dice(actor)
            damage_bonus = modifier(actor["stats"][actor["attack_stat"]]) + equipment_bonus(actor, self.content["items"], "damage_bonus")
            result.update(description=f"{weapon['name']}攻击 {target['id']} {target['name']}；命中加值 {bonus:+d}，对方 AC {target['ac']}；命中伤害 {weapon['dice']}{damage_bonus:+d}。", targets=[target["id"]], hit_probability=dice.hit_probability(bonus, target["ac"], adv, dis, self._blessed(actor)))
            result["attack_roll"] = self._attack_roll_info(state, actor, target)
            if 'divine_strike' in actor.get('passives', []) and not actor.get('divine_strike_used'):
                result['description'] += ' 本回合神圣打击可用：命中追加1d8光耀（暴击翻倍骰数）。'
        elif kind in ("cast", "ability"):
            ability_id = command.get("ability")
            if not isinstance(ability_id, str) or ability_id not in actor["abilities"]:
                raise GameError("unknown_ability", "此队员未拥有该能力。")
            ability = self.content["abilities"][ability_id]
            if "targets" in command and ability_id != "bless":
                raise GameError("invalid_target", "此能力只接受一个 target。")
            if "target" in command and ability_id == "bless":
                raise GameError("invalid_target", "祝福术请使用 targets 列表。")
            self._need(actor, ability["cost"])
            result["cost"] = {ability["cost"]: 1}
            spell_level = ability["level"]
            if spell_level or ability_id in ("fire_bolt","sacred_flame"):
                self._casting_equipment(actor)
                if actor.get("growth_version") and spell_level and ability_id not in actor.get("prepared",[]):
                    raise GameError("not_prepared","法术尚未准备。")
            if spell_level:
                slot = command.get("slot", spell_level)
                if type(slot) is not int or slot < spell_level or actor["slots"].get(str(slot), 0) <= 0:
                    raise GameError("invalid_slot", "没有指定环阶的合法法术位。")
                result["command"]["slot"] = slot
                result["cost"][f"slot_{slot}"] = 1
            elif "slot" in command:
                raise GameError("invalid_slot", "该能力不使用法术位。")
            if ability_id == "second_wind":
                if "target" in command and command["target"] != actor["id"]:
                    raise GameError("invalid_target", "回气只能治疗自己。")
                if actor["resources"].get("second_wind", 0) <= 0:
                    raise GameError("insufficient_resource", "回气已用完，短休后恢复。")
                result["cost"]["second_wind"] = 1
                targets = [actor]
            elif ability_id == "bless":
                target_ids = command.get("targets")
                if not isinstance(target_ids, list) or not 1 <= len(target_ids) <= 3 + result["command"]["slot"] - 1 or any(not isinstance(t, str) for t in target_ids) or len(set(target_ids)) != len(target_ids):
                    raise GameError("invalid_target", "祝福术需要 1 至 3 个不同队员目标（1 环）。")
                targets = [self._target(state, tid, "party") for tid in target_ids]
                if any(not self._alive(t) for t in targets):
                    raise GameError("invalid_target", "祝福术不能施于失能目标。")
            else:
                friendly = ability_id in ("healing_word", "cure_wounds", "mage_armor")
                target = self._target(state, command.get("target"), "party" if friendly else "enemy")
                if ability_id == "mage_armor" and (bool(target.get("equipment",{}).get("armor")) if "equipment" in target else target["template"] not in ("wizard", "monk")):
                    raise GameError("invalid_target", "法师护甲不能施于穿护甲的队员。")
                if ability_id == "mage_armor" and not self._alive(target):
                    raise GameError("invalid_target", "目标已经失能。")
                if ability_id == "unarmed_strike" and (actor.get("equipment",{}).get("armor") or actor.get("equipment",{}).get("off_hand")):
                    raise GameError("monk_armor","武艺需要无甲无盾。")
                if ability_id == "unarmed_strike" and self.content["items"][actor["weapon"]].get("category", "martial" if actor["weapon"]=="longsword" else "simple")=="martial":
                    raise GameError("invalid_ability","武艺附赠打击需要先用武僧武器攻击。")
                if ability_id == "unarmed_strike" and not actor["attacked"]:
                    raise GameError("invalid_ability", "武僧需先在本回合使用攻击动作，才能附赠徒手攻击。")
                targets = [target]
            for target in targets:
                self._in_range(state, actor, target, ability["range"])
            result["targets"] = [t["id"] for t in targets]
            result["description"] = f"{ability['name']} → {', '.join(result['targets'])}；{self._skill_summary(actor, ability_id, result['command'].get('slot'))}"
            if ability.get("concentration") and actor["concentration"]:
                result["warnings"].append("施放后会替换当前专注，并移除旧效果。")
            if ability_id in ("fire_bolt", "unarmed_strike"):
                adv, dis, height = self._attack_context(state, actor, targets[0], ranged=ability_id == "fire_bolt")
                attack_stat = actor["spell_stat"] if ability_id == "fire_bolt" else actor["attack_stat"]
                bonus = (self._spell_attack(actor) if ability_id == "fire_bolt" else self._weapon_proficiency(actor) + modifier(actor["stats"][attack_stat])) + height
                result["hit_probability"] = dice.hit_probability(bonus, targets[0]["ac"], adv, dis, self._blessed(actor))
                result["attack_roll"] = self._attack_roll_info(state, actor, targets[0], ranged=ability_id == "fire_bolt")
            elif ability_id == "sacred_flame":
                dc = self._spell_dc(actor)
                result["save_dc"] = dc
                result["failure_probability"] = self._save_failure_probability(targets[0], "dex", dc)
        elif kind == "help":
            target = self._target(state, command.get("target"), "party")
            self._in_range(state, actor, target, 1.5)
            if "downed" not in target["conditions"]:
                raise GameError("invalid_target", "救助只用于倒地队员。")
            result.update(targets=[target["id"]], description=f"救助 {target['id']}，恢复 1 生命并结束倒地状态。")
        elif kind == "potion":
            self._need(actor, "bonus")
            if actor["resources"]["potions"] <= 0:
                raise GameError("insufficient_resource", "治疗药水已用完。")
            result.update(cost={"bonus": 1, "potions": 1}, description="饮用治疗药水：恢复 2d4+2 生命。")
        elif kind == "dash":
            result["description"] = f"疾走：额外获得 {actor['speed']:g} 米移动。"
        elif kind == "disengage":
            result["description"] = "撤离：本回合移动不触发借机攻击。"
        elif kind == "dodge":
            result["description"] = "闪避：直到下次回合开始，针对自己的攻击具有劣势。"
        if kind == "potion" and actor["hp"] == actor["max_hp"]:
            result["warnings"].append("目标已满血，本次治疗不会恢复生命，仍会消耗资源。")
        if kind in ("cast", "ability"):
            for target_id in result["targets"]:
                target = state["actors"][target_id]
                if command.get("ability") in ("healing_word", "cure_wounds", "second_wind") and target["hp"] == target["max_hp"]:
                    result["warnings"].append(f"{target_id} 已满血，治疗可能浪费资源。")
                if command.get("ability") in target["effects"]:
                    result["warnings"].append(f"{target_id} 已有同名效果，重复施法可能浪费资源。")
        return result

    def _move_reactors(self, state, actor, steps):
        """Known opportunity threats, matching _continue_move's trigger rules."""
        if "disengaged" in actor["conditions"]:
            return []
        threats = []
        for reactor in state["actors"].values():
            if (reactor["side"] == actor["side"] or not self._alive(reactor)
                    or not self._visible(state, reactor) or reactor["budgets"]["reaction"] <= 0
                    or (reactor["side"] == "enemy" and reactor.get("range", 1.5) > 1.5)):
                continue
            if any(spatial.reach_distance(state["map"], reactor["position"], start) <= 1.5
                   < spatial.reach_distance(state["map"], reactor["position"], end)
                   and spatial.line_of_sight(state["map"], reactor["position"], start)
                   for start, end in zip(steps, steps[1:])):
                threats.append({"id": reactor["id"], "name": reactor["name"]})
        return threats

    def execute(self, state, command):
        work, events = self._execute(state, command)
        self._stamp_version(work)
        return work, events

    def _stamp_version(self, state):
        state.update(content_version=CAMPAIGN_VERSION if state.get('campaign') else CONTENT_VERSION,
                     rules_version=RULES_VERSION)

    def _execute(self, state, command):
        if isinstance(command, dict) and isinstance(command.get('type'), str) and command['type'] in CAMPAIGN_COMMANDS:
            self._check_version(state)
            return self._campaign_execute(state, command)
        if isinstance(command,dict) and command.get("type")=="practice":
            self._check_version(state)
            return self._practice_execute(state,command)
        if isinstance(command,dict) and command.get("type") in ("levelup","equip","prepare","recover"):
            self._check_version(state)
            return self._management_execute(state,command)
        checked = self.preview(state, command)
        work = deepcopy(state)
        before = work["event_seq"]
        command = checked["command"]
        kind = command["type"]
        if kind == "rest":
            work["short_rests"] -= 1
            for actor in work["actors"].values():
                if actor["side"] == "party" and "dead" not in actor["conditions"]:
                    self._heal(work, actor, (actor["max_hp"] + 1) // 2, "短休")
                    actor["resources"].update({k:v for k,v in actor["max_resources"].items() if k!="arcane_recovery"})
            self._emit(work, "rest", f"全队短休完成，剩余 {work['short_rests']} 次；法术位、药水未恢复，已死亡队员不会复活。")
        else:
            actor = work["actors"][command["actor"]]
            if kind == "move":
                self._continue_move(work, actor, checked["path"][1:])
            elif kind == "end":
                actor["ended"] = True
                self._expire_control(work, actor)
                self._emit(work, "turn_end", f"{actor['id']} 结束操作。")
            else:
                for resource, cost in checked["cost"].items():
                    if resource in actor["budgets"]:
                        actor["budgets"][resource] -= cost
                    elif resource.startswith("slot_"):
                        actor["slots"][resource[5:]] -= cost
                    else:
                        actor["resources"][resource] -= cost
                if checked['cost']:
                    self._emit(work, 'resource', f"{actor['id']} 本次消耗：" + '、'.join(f'{key} -{value}' for key, value in checked['cost'].items()), cost=deepcopy(checked['cost']))
                if kind == "attack":
                    actor["attacked"] = True
                    if actor.get("extra_attacks",0)>0: actor["extra_attacks"]-=1
                    else: actor["extra_attacks"]=actor.get("extra_attack",0)
                    self._weapon_attack(work, actor, work["actors"][command["target"]])
                elif kind in ("cast", "ability"):
                    self._cast(work, actor, command, checked["targets"])
                elif kind == "dash":
                    actor["budgets"]["movement"] += actor["speed"]
                    self._emit(work, "dash", f"{actor['id']} 疾走，获得 {actor['speed']:g} 米移动。")
                elif kind == "disengage":
                    if "disengaged" not in actor["conditions"]:
                        actor["conditions"].append("disengaged")
                    self._emit(work, "disengage", f"{actor['id']} 撤离，本回合免于借机攻击。")
                elif kind == "dodge":
                    if "dodge" not in actor["conditions"]:
                        actor["conditions"].append("dodge")
                    self._emit(work, "dodge", f"{actor['id']} 闪避，直到下次回合开始。")
                elif kind == "help":
                    self._heal(work, work["actors"][command["target"]], 1, "救助")
                elif kind == "potion":
                    work.setdefault("battle_summary", {}).setdefault("potions", 0)
                    work["battle_summary"]["potions"] += 1
                    amount, detail = dice.roll(work, "2d4+2")
                    self._heal(work, actor, amount, "治疗药水", detail)
            self._outcome(work)
            self._advance(work)
        work["revision"] = state["revision"] + 1
        return work, [deepcopy(e) for e in work["event_log"] if e["seq"] > before]

    def _cast(self, state, actor, command, target_ids):
        ability_id = command["ability"]
        if ability_id in TACTICAL:
            return self._tactical_cast(state, actor, command)
        if ability_id in DATA["abilities"]:
            return self._new_effect(state,actor,command,target_ids)
        effect = self.content["abilities"][ability_id]["effect"]
        target = state["actors"][target_ids[0]]
        slot = command.get("slot", 0)
        self._emit(state, "ability", f"{actor['id']} 使用 {self.content['abilities'][ability_id]['name']} → {', '.join(target_ids)}。", cost=self.content["abilities"][ability_id]["cost"], slot=slot)
        if effect == "fire_bolt":
            self._attack(state, actor, target, "2d10" if actor["level"]>=5 else "1d10", "fire", actor["spell_stat"], ranged=True, damage_modifier=0)
        elif effect == "sacred_flame":
            dc = self._spell_dc(actor)
            success = self._save(state, target, "dex", dc, "圣火术")
            if not success:
                amount, detail = dice.roll(state, "2d8" if actor["level"]>=5 else "1d8")
                self._damage(state, target, amount, "radiant", "圣火术", detail)
        elif effect in ("healing_word", "cure_wounds"):
            expression = f"{slot}d{4 if effect == 'healing_word' else 8}"
            amount, detail = dice.roll(state, expression)
            spell_bonus = modifier(actor["stats"][actor["spell_stat"]])
            life_bonus = 2 + slot if actor["template"] == "cleric" else 0
            gear_healing = equipment_bonus(actor, self.content["items"], "healing_bonus")
            detail.update(spell_modifier=spell_bonus, life_domain=life_bonus, equipment_bonus=gear_healing)
            previous_hp = target['hp']
            self._heal(state, target, amount + spell_bonus + life_bonus + gear_healing, self.content["abilities"][ability_id]["name"], detail)
            self._blessed_heal(state, actor, [target] if target['hp'] > previous_hp else [], slot)
        elif effect == "second_wind":
            amount, detail = dice.roll(state, f"1d10+{actor['level']}")
            self._heal(state, actor, amount, "回气", detail)
        elif effect == "unarmed_strike":
            self._attack(state, actor, target, "1d6" if actor["level"]>=3 else "1d4", "bludgeoning", actor["attack_stat"])
        elif effect == "mage_armor":
            target["ac"] = max(target["base_ac"], 13 + modifier(target["stats"]["dex"]))
            target["effects"]["mage_armor"] = {"source": actor["id"]}
            if target.get("growth_version"): recalculate(target,self.content["items"])
            self._emit(state, "effect", f"{target['id']} 获得法师护甲，AC {target['ac']}。")
        elif effect == "bless":
            self._drop_concentration(state, actor, "被新专注替换")
            actor["concentration"] = {"ability": "bless", "targets": list(target_ids), "remaining": 10}
            for target_id in target_ids:
                target_actor = state["actors"][target_id]
                # Independent casters remain independent sources, but still
                # grant just one d4. Ending one must preserve the others.
                existing = target_actor["effects"].get("bless", {})
                sources = existing.get("sources", [existing["source"]] if "source" in existing else [])
                sources = [s for s in sources if s != actor["id"]] + [actor["id"]]
                target_actor["effects"]["bless"] = {"source": sources[-1], "sources": sources}
            self._emit(state, "concentration", f"{actor['id']} 专注祝福术（10 回合），目标的攻击与豁免获得 1d4；同名效果不叠加。")

    def _unarmed_dice(self,a):
        eq=a.get('equipment',{})
        if a['template']=='monk' and not eq.get('armor') and not eq.get('off_hand'):
            return '1d6' if a['level']>=3 else '1d4'
        return '0d4+1'

    def _weapon_proficiency(self,a):
        if a['side']!='party' or not a.get('growth_version'): return proficiency(a)
        item=self.content['items'][a['weapon']]
        category=item.get('category', 'martial' if a['weapon']=='longsword' else 'simple')
        return proficiency(a) if a['weapon']=='monk_fists' or item.get('base_weapon', a['weapon']) in PROF[a['template']] or category in PROF[a['template']] else 0

    def _weapon_attack(self, state, attacker, target, reaction=False):
        if attacker["side"] == "party":
            item = self.content["items"][attacker["weapon"]]
            expression, damage_type, ranged = item["dice"], item["damage_type"], False
            if attacker['weapon']=='monk_fists': expression=self._unarmed_dice(attacker)
        else:
            expression, damage_type = attacker["dice"], attacker["damage_type"]
            ranged = attacker["range"] > 1.5 and not reaction
        return self._attack(state, attacker, target, expression, damage_type, attacker["attack_stat"], ranged=ranged, reaction=reaction, weapon_attack=True)

    def _attack(self, state, attacker, target, expression, damage_type, stat, ranged=False, damage_modifier=None, reaction=False, proficiency_override=None, weapon_attack=False):
        info = self._attack_roll_info(state, attacker, target, ranged)
        adv, dis, height = info["advantage"], info["disadvantage"], info["height_bonus"]
        natural, rolls = dice.d20(state, adv, dis)
        bless = dice.die(state, 4) if self._blessed(attacker) else 0
        ability_bonus = modifier(attacker["stats"][stat])
        prof = proficiency_override if proficiency_override is not None else (proficiency(attacker) if damage_modifier is not None else self._weapon_proficiency(attacker))
        gear_attack = equipment_bonus(attacker, self.content["items"], "attack_bonus" if weapon_attack else "spell_attack") if weapon_attack or damage_modifier is not None else 0
        bonus = prof + ability_bonus + height + gear_attack
        hit = natural == 20 or (natural != 1 and natural + bonus + bless >= target["ac"])
        critical = hit and (natural == 20 or ("downed" in target["conditions"] and spatial.reach_distance(state["map"], attacker["position"], target["position"]) <= 1.5))
        text = f"{attacker['id']} {'借机攻击' if reaction else '攻击'} {target['id']}：{dice.describe_d20(info, rolls, natural)}；命中加值 {bonus:+d}"
        if bless:
            text += f" + 祝福 {bless}"
        text += f" 对 AC {target['ac']}，{'暴击' if critical else '命中' if hit else '未命中'}。"
        self._emit(state, "attack", text, rolls=rolls, natural=natural, proficiency=prof, ability_modifier=ability_bonus, height_bonus=height, equipment_bonus=gear_attack, bless=bless, advantage=adv, disadvantage=dis, advantage_reasons=info["advantage_reasons"], disadvantage_reasons=info["disadvantage_reasons"], ac=target["ac"], hit=hit, critical=critical)
        if hit:
            amount, detail = dice.roll(state, expression, critical)
            extra = ability_bonus if damage_modifier is None else damage_modifier
            if weapon_attack: extra += equipment_bonus(attacker, self.content["items"], "damage_bonus")
            detail["ability_modifier"] = extra
            self._damage(state, target, max(0, amount + extra), damage_type, "借机攻击" if reaction else "攻击", detail, critical)
            if weapon_attack and not reaction and 'divine_strike' in attacker.get('passives', []) and not attacker.get('divine_strike_used'):
                attacker['divine_strike_used'] = True
                if self._alive(target):
                    radiant, radiant_detail = dice.roll(state, '1d8', critical)
                    self._damage(state, target, radiant, 'radiant', '神圣打击', radiant_detail, critical)
        return hit

    def _save_failure_probability(self, target, stat, dc):
        """Pure counterpart of _save, including shared Bless and Dodge rules."""
        if "stunned" in target["conditions"] and stat in ("str", "dex"): return 1.0
        bonus = modifier(target["stats"][stat]) + (proficiency(target) if stat in target["save_proficiencies"] else 0) + equipment_bonus(target, self.content['items'], 'save_bonus')
        bonuses = [bonus + b for b in range(1, 5)] if self._blessed(target) else [bonus]
        advantage = stat == "dex" and "dodge" in target["conditions"] and self._alive(target)
        return sum((sum(n + b < dc for n in range(1, 21)) / 20) ** (2 if advantage else 1)
                   for b in bonuses) / len(bonuses)

    def _save(self, state, target, stat, dc, reason):
        if "stunned" in target["conditions"] and stat in ("str", "dex"):
            self._emit(state, "save", f"{target['id']} {reason}：震慑使{stat}豁免自动失败。", dc=dc, success=False, automatic=True)
            return False
        advantage = stat == "dex" and "dodge" in target["conditions"] and self._alive(target)
        natural, rolls = dice.d20(state, advantage=advantage)
        prof = proficiency(target) if stat in target["save_proficiencies"] else 0
        gear_save = equipment_bonus(target, self.content['items'], 'save_bonus')
        bonus = modifier(target["stats"][stat]) + prof + gear_save
        bless = dice.die(state, 4) if self._blessed(target) else 0
        success = natural + bonus + bless >= dc
        info = dict(advantage=advantage, advantage_reasons=["闪避使敏捷豁免获得优势"] if advantage else [])
        self._emit(state, "save", f"{target['id']} {reason} {stat} 豁免：{dice.describe_d20(info, rolls, natural)}；加值 {bonus:+d} + 祝福 {bless} 对 DC {dc}，{'成功' if success else '失败'}。", rolls=rolls, natural=natural, modifier=bonus, proficiency=prof, equipment_bonus=gear_save, advantage=advantage, advantage_reasons=info["advantage_reasons"], bless=bless, dc=dc, success=success)
        return success

    def _drop_concentration(self, state, actor, reason):
        if not actor["concentration"]:
            return
        ability = actor['concentration']['ability']
        actor['last_concentration_end'] = dict(ability=ability, name=self.content['abilities'][ability]['name'], reason=reason, round=state['round'])
        for target in state["actors"].values():
            for effect_id in list(target["effects"]):
                if effect_id == "bless":
                    effect = target["effects"][effect_id]
                    sources = effect.get("sources", [effect["source"]])
                    remaining = [s for s in sources if s != actor["id"]]
                    if remaining:
                        target["effects"][effect_id] = {"source": remaining[-1], "sources": remaining}
                    else:
                        del target["effects"][effect_id]
        actor["concentration"] = None
        self._emit(state, "concentration_end", f"{actor['id']} 【专注结束】{self.content['abilities'][ability]['name']}：{reason}；本次专注维持的效果已移除。", actor=actor['id'], ability=ability, reason=reason)

    def _damage(self, state, target, amount, damage_type, source, detail=None, critical=False):
        original = amount
        if damage_type in target.get("immunities", []):
            amount = 0
        else:
            if damage_type in target.get("resistances", []):
                amount //= 2
            if damage_type in target.get("vulnerabilities", []):
                amount *= 2
        temporary = min(target.get("temp_hp", 0), amount)
        target["temp_hp"] = target.get("temp_hp", 0) - temporary
        damage = amount - temporary
        was_downed = "downed" in target["conditions"]
        previous_hp = target["hp"]
        target["hp"] = max(0, previous_hp - damage)
        if target['hp'] == 0 and previous_hp > 0 and target['effects'].pop('death_ward', None):
            target['hp'] = 1
            self._emit(state, 'effect', f"{target['id']} 死亡防护消耗：本次伤害后保留1生命。")
        damage_label = {"force":"力场", "fire":"火焰", "slashing":"挥砍", "piercing":"穿刺", "radiant":"光耀", "bludgeoning":"钝击"}.get(damage_type, damage_type)
        self._emit(state, "damage", f"{target['id']} 受到 {source} 的 {amount} 点 {damage_label} 伤害（临时生命吸收 {temporary}），生命 {target['hp']}/{target['max_hp']}。", **(detail or {}), raw_damage=original, applied_damage=amount, absorbed=temporary, damage_type=damage_type)
        self._boss_transition(state, target)
        if target["hp"] == 0 and amount > 0:
            if target["side"] == "enemy":
                target["conditions"] = ["dead"]
                target["ended"] = True
                self._emit(state, "defeat_enemy", f"{target['id']} 被击败。")
            else:
                if not was_downed:
                    target["conditions"] = [c for c in target["conditions"] if c not in ("dodge", "disengaged", "stable")]
                    target["conditions"].append("downed")
                    target["death_success"] = target["death_failure"] = 0
                    target["ended"] = True
                    self._emit(state, "downed", f"{target['id']} 倒地，可以治疗或救助；并非直接死亡。")
                elif damage > 0:
                    target["death_failure"] += 2 if critical else 1
                    if "stable" in target["conditions"]:
                        target["conditions"].remove("stable")
                    self._death_status(state, target)
                self._drop_concentration(state, target, "倒地失能")
        elif amount > 0 and target["concentration"]:
            if not self._save(state, target, "con", max(10, amount // 2), "专注"):
                self._drop_concentration(state, target, "受伤豁免失败")

    def _heal(self, state, target, amount, source, detail=None):
        if "dead" in target["conditions"]:
            return
        previous = target["hp"]
        target["hp"] = min(target["max_hp"], target["hp"] + max(0, amount))
        if target["hp"] > 0:
            if previous == 0 and target["id"] in state["active_group"] and target.get("last_turn_round") == state["round"]:
                target["ended"] = False
            target["conditions"] = [c for c in target["conditions"] if c not in ("downed", "stable")]
            target["death_success"] = target["death_failure"] = 0
        if target["hp"] == previous:
            return
        self._emit(state, "heal", f"{target['id']} 获得 {source} 治疗 {target['hp'] - previous} 点，生命 {target['hp']}/{target['max_hp']}。", **(detail or {}), rolled_healing=amount, actual_healing=target["hp"] - previous)

    def _death_status(self, state, actor):
        if actor["death_failure"] >= 3:
            actor["death_failure"] = 3
            actor["conditions"] = ["dead"]
            self._emit(state, "death", f"{actor['id']} 死亡豁免失败三次，死亡。")
        elif actor["death_success"] >= 3 and "stable" not in actor["conditions"]:
            actor["conditions"].append("stable")
            self._emit(state, "stable", f"{actor['id']} 死亡豁免成功三次，伤势稳定，仍需治疗。")

    def _start_actor(self, state, actor):
        actor["conditions"] = [c for c in actor["conditions"] if c not in ("dodge", "disengaged")]
        actor["budgets"] = {"action": 1, "bonus": 1, "reaction": 1, "movement": actor["speed"]}
        actor["attacked"] = False
        actor["extra_attacks"] = 0
        actor['divine_strike_used'] = False
        actor["last_turn_round"] = state["round"]
        actor["ended"] = not self._alive(actor)
        if "downed" in actor["conditions"] and "stable" not in actor["conditions"]:
            natural = dice.die(state, 20)
            self._emit(state, "death_save", f"{actor['id']} 死亡豁免 d20 = {natural}。", roll=natural)
            if natural == 20:
                self._heal(state, actor, 1, "死亡豁免自然20")
                actor["ended"] = False
            elif natural >= 10:
                actor["death_success"] += 1
            else:
                actor["death_failure"] += 2 if natural == 1 else 1
            self._death_status(state, actor)
        if actor["concentration"]:
            actor["concentration"]["remaining"] -= 1
            if actor["concentration"]["remaining"] <= 0:
                self._drop_concentration(state, actor, "持续时间结束")
        if "stunned" in actor["conditions"]:
            actor["budgets"] = {key: 0 for key in actor["budgets"]}
            actor["ended"] = True
            self._emit(state, "turn_skip", f"{actor['id']} 被震慑，本次回合无法行动。")

    def _outcome(self, state):
        if state["phase"] != "combat":
            return
        living_enemies = [a for a in state["actors"].values() if a["side"] == "enemy" and self._alive(a)]
        living_party = [a for a in state["actors"].values() if a["side"] == "party" and self._alive(a)]
        if not living_enemies:
            state["phase"], state["outcome"] = "victory", "victory"
            state["pending"] = None
            state["active_group"] = []
            if state.get('campaign'):
                self._campaign_reward(state)
            else:
                self._emit(state, "victory", "前哨战胜利！可进入成长、装备、独立演练，或新开七站远征战役。")
        elif not living_party:
            state["phase"], state["outcome"] = "defeated", "defeated"
            state["pending"] = None
            state["active_group"] = []
            self._emit(state, "defeated", "四人均失去战斗能力，战斗失败。retry 可原样恢复战前血量、资源和随机数。")

    def _advance(self, state):
        # Only commands drive the loop; no timers or background combat workers.
        while state["phase"] == "combat" and not state["pending"]:
            self._outcome(state)
            if state["phase"] != "combat":
                return
            if state["active_group"]:
                first = state["actors"][state["active_group"][0]]
                if first["side"] == "party":
                    if self._active(state):
                        return
                else:
                    unfinished = [state["actors"][aid] for aid in state["active_group"] if not state["actors"][aid]["ended"] and self._alive(state["actors"][aid])]
                    if unfinished:
                        self._enemy_turn(state, unfinished[0])
                        continue
                for aid in state["active_group"]:
                    self._expire_control(state, state["actors"][aid])
                state["active_group"] = []
            if state["cursor"] >= len(state["turn_order"]):
                state["cursor"] = 0
                state["round"] += 1
                self._emit(state, "round", f"第 {state['round']} 回合开始。")
            first_id = state["turn_order"][state["cursor"]]
            side = state["actors"][first_id]["side"]
            while state["cursor"] < len(state["turn_order"]):
                actor_id = state["turn_order"][state["cursor"]]
                if state["actors"][actor_id]["side"] != side:
                    break
                state["cursor"] += 1
                state["active_group"].append(actor_id)
                if "dead" not in state["actors"][actor_id]["conditions"]:
                    self._start_actor(state, state["actors"][actor_id])
            if side == "party" and self._active(state):
                self._emit(state, "turn", f"可操作：{', '.join(self._active(state))}；先攻相邻的队员可以交错行动。")

    def _continue_move(self, state, mover, remaining, handled=None, after=None):
        handled = list(handled or [])
        while remaining and self._alive(mover) and state["phase"] == "combat":
            destination = remaining[0]
            occupants = [a for a in state["actors"].values() if a["id"] != mover["id"] and a["position"] == destination and "dead" not in a["conditions"]]
            if occupants:
                if mover["side"] == "party":
                    self._emit(state, "movement_stop", f"{mover['id']} 的前进路径被占用，保留已完成移动。")
                break
            if "disengaged" not in mover["conditions"]:
                for reactor in sorted(state["actors"].values(), key=lambda a: a["id"]):
                    if reactor["id"] in handled or reactor["side"] == mover["side"] or not self._alive(reactor) or reactor["budgets"]["reaction"] <= 0:
                        continue
                    # Ranged-only enemies do not invent a melee reaction weapon.
                    if reactor["side"] == "enemy" and reactor.get("range", 1.5) > 1.5:
                        continue
                    near = spatial.reach_distance(state["map"], reactor["position"], mover["position"]) <= 1.5
                    leaving = spatial.reach_distance(state["map"], reactor["position"], destination) > 1.5
                    if near and leaving and spatial.line_of_sight(state["map"], reactor["position"], mover["position"]):
                        handled.append(reactor["id"])
                        if reactor["side"] == "party":
                            state["pending"] = {"kind": "opportunity", "reactor": reactor["id"], "mover": mover["id"], "path": list(remaining), "handled": handled, "after": deepcopy(after)}
                            self._emit(state, "reaction", f"{reactor['id']} 可对 {mover['id']} 借机攻击；请从反应菜单选择，战斗暂停。")
                            return
                        reactor["budgets"]["reaction"] -= 1
                        self._weapon_attack(state, reactor, mover, reaction=True)
                        self._outcome(state)
                        if not self._alive(mover) or state["phase"] != "combat":
                            break
            if not self._alive(mover) or state["phase"] != "combat":
                break
            price = dict(spatial.neighbors(state["map"], mover["position"])).get(destination)
            if price is None or mover["budgets"]["movement"] < price:
                break
            was_visible = self._visible(state, mover)
            source = mover["position"]
            mover["position"] = destination
            mover["budgets"]["movement"] -= price
            now_visible = self._visible(state, mover)
            if now_visible:
                self._emit(state, "move", f"{mover['id']} 移动至 {destination}，消耗 {price:g} 米。", destination=destination, cost=price)
            elif was_visible:
                self._emit(state, "lost_sight", f"{mover['id']} 离开视线。")
            if destination in state["map"].get("hazards", {}):
                hazard = state["map"]["hazards"][destination]
                # Hidden movement and its consequences cannot leak coordinates.
                if now_visible:
                    self._damage(state, mover, hazard["damage"], "fire", hazard["description"])
                else:
                    mover["hp"] = max(0, mover["hp"] - hazard["damage"])
                    if mover["hp"] == 0:
                        mover["conditions"] = ["dead"]
                        mover["ended"] = True
            remaining = remaining[1:]
            handled = []
            self._outcome(state)
        if after and after["type"] == "enemy_turn" and state["phase"] == "combat":
            self._enemy_finish(state, mover)

    def react(self, state, choice):
        self._check_version(state)
        if choice not in ("accept", "decline"):
            raise GameError("invalid_reaction", "反应选择为 accept 或 decline。")
        if not state["pending"] or state["phase"] != "combat":
            raise GameError("no_reaction", "当前没有待选反应。")
        work = deepcopy(state)
        before = work["event_seq"]
        pending = work["pending"]
        work["pending"] = None
        reactor, mover = work["actors"][pending["reactor"]], work["actors"][pending["mover"]]
        if choice == "accept":
            if not self._alive(reactor) or reactor["budgets"]["reaction"] <= 0:
                raise GameError("invalid_reaction", "该队员已无法使用反应。")
            reactor["budgets"]["reaction"] -= 1
            self._weapon_attack(work, reactor, mover, reaction=True)
            self._outcome(work)
        else:
            self._emit(work, "reaction_declined", f"{reactor['id']} 放弃借机攻击，保留反应。")
        if work["phase"] == "combat":
            self._continue_move(work, mover, pending["path"], pending["handled"], pending["after"])
            self._advance(work)
        work["revision"] = state["revision"] + 1
        self._stamp_version(work)
        return work, [deepcopy(e) for e in work["event_log"] if e["seq"] > before]

    def _enemy_targets(self, state, enemy):
        return [a for a in state["actors"].values() if a["side"] == "party" and self._alive(a) and spatial.distance(state["map"], enemy["position"], a["position"]) <= 18 and spatial.line_of_sight(state["map"], enemy["position"], a["position"])]

    def _enemy_turn(self, state, enemy):
        targets = self._enemy_targets(state, enemy)
        if not targets:
            enemy["ended"] = True
            if self._visible(state, enemy):
                self._emit(state, "enemy_wait", f"{enemy['id']} 未发现可见目标，守住位置。")
            return
        in_range = [a for a in targets if (spatial.reach_distance if enemy["range"] <= 1.5 else spatial.distance)(state["map"], enemy["position"], a["position"]) <= enemy["range"]]
        retreat = enemy["range"] > 1.5 and any(spatial.distance(state["map"], enemy["position"], a["position"]) <= 1.5 for a in targets)
        if in_range and not retreat:
            self._enemy_finish(state, enemy)
            return
        occupied = {a["position"] for a in state["actors"].values() if a["id"] != enemy["id"] and "dead" not in a["conditions"] and (a["side"] == "enemy" or (spatial.distance(state["map"], enemy["position"], a["position"]) <= 18 and spatial.line_of_sight(state["map"], enemy["position"], a["position"])))}
        candidates = []
        for target in targets:
            for y in range(state["map"]["height"]):
                for x in range(state["map"]["width"]):
                    cell = spatial.label(x, y)
                    if cell in occupied or cell in state["map"].get("hazards", {}):
                        continue
                    if retreat and any(spatial.distance(state["map"], cell, known["position"]) <= 1.5 for known in targets):
                        continue
                    if (spatial.reach_distance if enemy["range"] <= 1.5 else spatial.distance)(state["map"], cell, target["position"]) > enemy["range"] or not spatial.line_of_sight(state["map"], cell, target["position"]):
                        continue
                    route = spatial.path(state["map"], enemy["position"], cell, occupied, avoid_hazards=True)
                    if route:
                        candidates.append((route[1], target["hp"], cell, route[0]))
        if not candidates:
            self._enemy_finish(state, enemy)
            return
        route = min(candidates)[3][1:]
        usable, budget, source = [], enemy["budgets"]["movement"], enemy["position"]
        for destination in route:
            price = dict(spatial.neighbors(state["map"], source))[destination]
            if budget < price:
                break
            usable.append(destination)
            budget -= price
            source = destination
        self._continue_move(state, enemy, usable, after={"type": "enemy_turn", "actor": enemy["id"]})

    def _enemy_finish(self, state, enemy):
        if self._alive(enemy) and enemy["budgets"]["action"]:
            targets = [a for a in self._enemy_targets(state, enemy) if (spatial.reach_distance if enemy["range"] <= 1.5 else spatial.distance)(state["map"], enemy["position"], a["position"]) <= enemy["range"]]
            if targets:
                target = min(targets, key=lambda a: (a["hp"], spatial.distance(state["map"], enemy["position"], a["position"]), a["id"]))
                # Attacking reveals this slice's hidden enemies before any
                # player-facing event can identify the attacker.
                enemy["hidden"] = False
                enemy["budgets"]["action"] -= 1
                if enemy.get('boss_phase') == 2:
                    self._boss_attack(state, enemy, target)
                elif enemy.get('spell'):
                    spell = enemy['spell']
                    self._emit(state, 'ability', f"{enemy['id']} 施放熔火箭 → {target['id']}。")
                    amount, detail = dice.roll(state, enemy['dice'])
                    self._save_damage(state, target, amount, enemy['damage_type'], spell['save'], spell['dc'], '熔火箭', detail)
                else:
                    self._weapon_attack(state, enemy, target)
                self._outcome(state)
        enemy["ended"] = True

    def retry(self, state):
        self._check_version(state)
        if state["phase"] != "defeated":
            raise GameError("invalid_retry", "仅战斗失败后可以重试。")
        work = deepcopy(state["battle_snapshot"])
        work["battle_snapshot"] = deepcopy(state["battle_snapshot"])
        work["event_log"] = deepcopy(state["event_log"])
        work["event_seq"] = state["event_seq"]
        before = work["event_seq"]
        self._emit(work, "retry", "恢复原始战前快照；没有额外治疗、奖励或重新生成敌人。")
        self._advance(work)
        work["revision"] = state["revision"] + 1
        self._stamp_version(work)
        return work, [deepcopy(e) for e in work["event_log"] if e["seq"] > before]
