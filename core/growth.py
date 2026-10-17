"""M3 fixed snapshots and equipment. Legacy actors remain unchanged until explicit upgrade."""

from copy import deepcopy
import json
from pathlib import Path


def load_growth(path=None):
    from .content import _JSONObjectPairs, _duplicate_checked, _invalid_constant

    source = (
        Path(path)
        if path
        else Path(__file__).resolve().parents[1] / "content/growth.json"
    )
    data = _duplicate_checked(
        json.loads(
            source.read_text(encoding="utf-8"),
            object_pairs_hook=_JSONObjectPairs,
            parse_constant=_invalid_constant,
        )
    )
    validate_growth(data)
    return data


def validate_growth(data):
    effects = {
        "action_surge",
        "rally",
        "flurry",
        "patient_defence",
        "scorching_ray",
        "fireball",
        "aid",
        "mass_healing_word",
        "preserve_life",
    }
    if (
        data.get("version") != 1
        or set(data.get("classes", {})) != {"fighter", "monk", "wizard", "cleric"}
        or set(data.get("abilities", {})) != effects
    ):
        raise ValueError("growth: version/classes/effect registry mismatch")
    from .content import (
        load_content,
        _dice,
        _stats,
        _number,
        _object,
        _save_proficiencies,
    )

    base = load_content()
    abilities = base["abilities"] | data["abilities"]
    for key, b in data["abilities"].items():
        if (
            b.get("effect") != key
            or not b.get("name")
            or not b.get("description")
            or b.get("cost") not in ("free", "action", "bonus")
        ):
            raise ValueError("growth: unimplemented effect or metadata")
        specs = {
            "action_surge": ("free", 0, 0, "action_surge"),
            "rally": ("bonus", 0, 9, "superiority"),
            "flurry": ("bonus", 0, 1.5, "ki"),
            "patient_defence": ("bonus", 0, 0, "ki"),
            "scorching_ray": ("action", 2, 18, None),
            "fireball": ("action", 3, 18, None),
            "aid": ("action", 2, 9, None),
            "mass_healing_word": ("bonus", 3, 18, None),
            "preserve_life": ("action", 0, 9, "channel"),
        }
        if (b["cost"], b["level"], b["range"], b.get("resource")) != specs[key]:
            raise ValueError("growth: effect metadata mismatch")
        _number(b.get("level"), "growth ability level", 0, 3, integer=True)
        _number(b.get("range"), "growth ability range", 0, 18)
    for cid, levels in data["classes"].items():
        if set(levels) != {str(n) for n in range(1, 6)}:
            raise ValueError("growth: requires five snapshots")
        for level, b in levels.items():
            _object(
                b,
                "growth snapshot",
                {
                    "level",
                    "stats",
                    "hp",
                    "proficiency",
                    "slots",
                    "resources",
                    "abilities",
                    "extra_attack",
                    "spell_stat",
                    "save_proficiencies",
                    "equipment",
                    "proficiencies",
                    "speed",
                    "passives",
                },
                {"always_prepared"},
            )
            _save_proficiencies(b["save_proficiencies"], "growth saves", required=True)
            _number(b["speed"], "growth speed", 1.5, 15)
            if set(b["passives"]) - {
                "defence",
                "unarmoured_defence",
                "martial_arts",
                "sculpt_spells",
                "disciple_of_life",
            }:
                raise ValueError("growth: unknown passive")
            items = base["items"] | data["items"]
            if set(b["equipment"]) != {"main_hand", "off_hand", "armor"} or any(
                item is not None and (item not in items or items[item]["slot"] != slot)
                for slot, item in b["equipment"].items()
            ):
                raise ValueError("growth: invalid default equipment")
            if set(b.get("always_prepared", [])) - set(b["abilities"]):
                raise ValueError("growth: unknown domain spell")
            if type(b["level"]) != int or b["level"] != int(level):
                raise ValueError("growth: invalid snapshot level")
            _stats(b["stats"], "growth stats", 3, 20)
            _number(b["hp"], "growth hp", 1, 100, integer=True)
            if type(b["proficiency"]) != int or b["proficiency"] != 2 + (
                int(level) >= 5
            ):
                raise ValueError("growth: invalid proficiency")
            if len(set(b["abilities"])) != len(b["abilities"]) or any(
                a not in abilities for a in b["abilities"]
            ):
                raise ValueError("growth: unknown or duplicate ability")
            for slot, count in b["slots"].items():
                if slot not in ("1", "2", "3") or int(slot) > (int(level) + 1) // 2:
                    raise ValueError("growth: illegal slot")
                _number(count, "growth slot", 1, 4, integer=True)
            for resource, count in b["resources"].items():
                if resource not in (
                    "second_wind",
                    "action_surge",
                    "superiority",
                    "ki",
                    "channel",
                    "arcane_recovery",
                ):
                    raise ValueError("growth: unknown resource")
                _number(count, "growth resource", 1, 6, integer=True)
            if type(b["extra_attack"]) != int or b["extra_attack"] != int(
                int(level) >= 5 and cid in ("fighter", "monk")
            ):
                raise ValueError("growth: invalid extra attack")
            if any(
                abilities[a]["level"] and str(abilities[a]["level"]) not in b["slots"]
                for a in b["abilities"]
            ):
                raise ValueError("growth: ability unlocked without slot")
    for key, b in data["items"].items():
        if (
            b.get("kind") not in ("weapon", "armor", "shield")
            or b.get("slot")
            != {"weapon": "main_hand", "armor": "armor", "shield": "off_hand"}[
                b["kind"]
            ]
        ):
            raise ValueError("growth: invalid equipment slot")
        if b["kind"] == "weapon":
            _dice(b.get("dice"), "growth weapon")
        else:
            _number(b.get("ac"), "growth armor", 1, 20, integer=True)
        if b.get("category") not in (
            "simple",
            "martial",
            "light",
            "medium",
            "heavy",
            "shield",
        ):
            raise ValueError("growth: invalid proficiency")


DATA = load_growth()
SELF = {"action_surge", "patient_defence", "mass_healing_word", "preserve_life", "aid"}
FRIENDLY = {"rally"}
PROF = {
    "fighter": {"simple", "martial", "light", "medium", "heavy", "shield"},
    "monk": {"simple"},
    "wizard": {"dagger", "staff"},
    "cleric": {"simple", "light", "medium", "heavy", "shield"},
}


def mod(n):
    return (n - 10) // 2


def proficiency(actor):
    return actor.get("proficiency", 2)


def snapshot(actor, level, items):
    old_max = actor["max_hp"]
    s = deepcopy(DATA["classes"][actor["template"]][str(level)])
    actor.update(
        {
            k: v
            for k, v in s.items()
            if k not in ("hp", "slots", "resources", "equipment")
        }
    )
    actor["max_hp"] = s["hp"] + actor.get("effects", {}).get("aid", {}).get("amount", 0)
    actor["hp"] = (
        min(actor["max_hp"], actor["hp"] + actor["max_hp"] - old_max)
        if actor["hp"] > 0
        else 0
    )
    # Preserve used resources, including across repeated upgrades.
    for current, maximum, incoming in [
        ("slots", "max_slots", s["slots"]),
        ("resources", "max_resources", s["resources"]),
    ]:
        used = {k: v - actor[current].get(k, 0) for k, v in actor[maximum].items()}
        actor[maximum] = deepcopy(incoming)
        actor[current] = {
            k: max(0, v - used.get(k, 0)) for k, v in incoming.items()
        } | (
            {"potions": actor["resources"].get("potions", 2)}
            if current == "resources"
            else {}
        )
    actor.setdefault("equipment", deepcopy(s["equipment"]))
    actor.setdefault("inventory", list(items))
    actor["known_spells"] = [
        a
        for a in s["abilities"]
        if a in ("magic_missile", "mage_armor", "healing_word", "cure_wounds", "bless")
        or DATA["abilities"].get(a, {}).get("level", 0) > 0
    ]
    actor["prepared"] = list(
        dict.fromkeys(
            actor.get("prepared", actor["known_spells"])
            + [
                a
                for a in actor["known_spells"]
                if a not in actor.get("known_before", actor["known_spells"])
            ]
            + actor.get("always_prepared", [])
        )
    )
    actor["known_before"] = list(actor["known_spells"])
    actor["growth_version"] = 1
    recalculate(actor, items)


def recalculate(a, items):
    eq = a["equipment"]
    dex = mod(a["stats"]["dex"])
    armor = items.get(eq.get("armor"), {})
    category = armor.get("category")
    ac = armor.get("ac", 10) + (
        0 if category == "heavy" else min(2, dex) if category == "medium" else dex
    )
    if not armor and a["template"] == "monk" and not eq.get("off_hand"):
        ac += mod(a["stats"]["wis"])
    ac += items.get(eq.get("off_hand"), {}).get("ac", 0)
    if armor and "defence" in a.get("passives", []):
        ac += 1
    a["base_ac"] = ac
    if "mage_armor" in a["effects"] and not armor:
        ac = max(ac, 13 + dex + items.get(eq.get("off_hand"), {}).get("ac", 0))
    a["ac"] = ac
    a["weapon"] = eq.get("main_hand") or "monk_fists"
    item = items[a["weapon"]]
    monk_weapon = (
        item.get("category", "martial" if a["weapon"] == "longsword" else "simple")
        == "simple"
    )
    finesse = a["weapon"] == "dagger" or (
        a["template"] == "monk" and monk_weapon and not armor and not eq.get("off_hand")
    )
    a["attack_stat"] = (
        "dex" if finesse and a["stats"]["dex"] >= a["stats"]["str"] else "str"
    )
    if a["template"] == "monk":
        a["speed"] = (
            12 if a["level"] >= 2 and not armor and not eq.get("off_hand") else 9
        )


class GrowthMixin:
    def _practice_preview(self, state, command):
        if (
            command != {"type": "practice"}
            or not state.get("development")
            or state["phase"] != "victory"
            or state["pending"]
        ):
            self._growth_error(
                "invalid_practice", "重新演练仅用于独立开发试玩的胜利阶段。"
            )
        return dict(
            command=deepcopy(command),
            revision=state["revision"],
            cost={},
            targets=[],
            warnings=["演练会刷新全部生命、药水和资源；没有经验、奖励或战役推进。"],
            description="沿用当前等级、准备法术与装备，重新演练前哨战。",
        )

    def _practice_execute(self, state, command):
        self._practice_preview(state, command)
        from .engine import Engine

        party = [state["actors"][f"P{i}"] for i in range(1, 5)]
        setup = []
        for a in party:
            # Remove the default shield before installing a two-handed loadout.
            setup.append(dict(type="equip", actor=a["id"], slot="off_hand", item=None))
            setup.extend(
                dict(type="equip", actor=a["id"], slot=k, item=v)
                for k, v in a["equipment"].items()
            )
            setup.append(dict(type="prepare", actor=a["id"], spells=a["prepared"]))
        fresh = Engine.new_run(
            self,
            state["name"],
            [a["template"] for a in party],
            state["rng_state"],
            levels=[a["level"] for a in party],
            loadouts=setup,
        )
        fresh["id"] = state["id"]
        fresh["development"] = True
        fresh["battle_snapshot"]["id"] = state["id"]
        fresh["battle_snapshot"]["development"] = True
        initial_events = fresh["event_log"]
        fresh["event_log"] = deepcopy(state["event_log"])
        fresh["event_seq"] = state["event_seq"]
        self._emit(
            fresh, "practice", "重新演练：已刷新生命与全部资源，沿用当前成长及装备。"
        )
        for event in initial_events:
            fresh["event_seq"] += 1
            fresh["event_log"].append({**event, "seq": fresh["event_seq"]})
        fresh["event_log"] = fresh["event_log"][-100:]
        fresh["revision"] = state["revision"] + 1
        return fresh, [
            deepcopy(e) for e in fresh["event_log"] if e["seq"] > state["event_seq"]
        ]

    def _growth_error(self, code, text):
        from .engine import GameError

        raise GameError(code, text)

    def _management_preview(self, state, c):
        kind = c["type"]
        actor_id = c.get("actor")
        a = state["actors"].get(actor_id) if isinstance(actor_id, str) else None
        fields = {
            "levelup": {"type", "actor"},
            "equip": {"type", "actor", "slot", "item"},
            "prepare": {"type", "actor", "spells"},
            "recover": {"type", "actor", "slot"},
        }
        if set(c) - fields[kind] or not a or a["side"] != "party":
            self._growth_error("invalid_command", "成长或装备命令参数无效。")
        if state["pending"] or state["phase"] != "victory":
            self._growth_error(
                "unsafe_management",
                "成长、换装与准备仅在胜利后的安全阶段开放；开发试玩可配置战前组合。",
            )
        if kind == "levelup":
            if a["level"] >= 5:
                self._growth_error("level_cap", "本轮等级上限为5。")
            description = f"固定职业成长：{a['level']} → {a['level']+1} 级，保留已消耗资源与倒地状态。"
        else:
            if not a.get("growth_version"):
                self._growth_error(
                    "legacy_actor", "旧角色请先显式升级，建立成长与装备快照。"
                )
            if kind == "recover":
                slot = c.get("slot")
                if (
                    type(slot) != int
                    or slot not in (1, 2, 3)
                    or a["resources"].get("arcane_recovery", 0) < slot
                    or a["slots"].get(str(slot), 0) >= a["max_slots"].get(str(slot), 0)
                ):
                    self._growth_error(
                        "invalid_recovery",
                        "没有足够奥术回想点数，或该环法术位无需恢复。",
                    )
                description = f"奥术回想：消耗{slot}点，恢复一个{slot}环法术位；短休不恢复回想点数。"
            elif kind == "equip":
                eq = deepcopy(a["equipment"])
                slot = c.get("slot")
                item = c.get("item")
                if not isinstance(slot, str) or slot not in eq:
                    self._growth_error("invalid_equipment", "不存在的装备位。")
                if item is not None and (
                    not isinstance(item, str)
                    or item not in a["inventory"]
                    or item not in self.content["items"]
                    or self.content["items"][item]["slot"] != slot
                ):
                    self._growth_error(
                        "invalid_equipment", "物品不在背包中或不适合此装备位。"
                    )
                eq[slot] = item
                if self.content["items"].get(eq.get("main_hand"), {}).get(
                    "two_handed"
                ) and eq.get("off_hand"):
                    self._growth_error(
                        "invalid_equipment", "双手武器不能同时持盾，请先卸下盾牌。"
                    )
                description = (
                    "装备调整："
                    + str(slot)
                    + " → "
                    + (self.content["items"][item]["name"] if item else "卸下")
                )
            else:
                spells = c.get("spells")
                limit = (
                    a["level"] + mod(a["stats"][a["spell_stat"]])
                    if a["spell_stat"]
                    else 0
                )
                if (
                    not isinstance(spells, list)
                    or any(
                        not isinstance(x, str)
                        or x not in a["known_spells"]
                        or not self.content["abilities"][x]["level"]
                        for x in spells
                    )
                    or len(set(spells)) != len(spells)
                    or len(set(spells) - set(a.get("always_prepared", []))) > limit
                    or not set(a.get("always_prepared", [])).issubset(spells)
                ):
                    self._growth_error(
                        "invalid_preparation",
                        "准备法术必须已学、无重复且不超过等级+施法属性修正。",
                    )
                description = "准备法术：" + "、".join(
                    self.content["abilities"][x]["name"] for x in spells
                )
        return dict(
            command=deepcopy(c),
            revision=state["revision"],
            cost={},
            targets=[a["id"]],
            warnings=[],
            description=description,
        )

    def _management_execute(self, state, c):
        self._management_preview(state, c)
        work = deepcopy(state)
        a = work["actors"][c["actor"]]
        if c["type"] == "levelup":
            snapshot(a, a["level"] + 1, self.content["items"])
            work["content_version"] = self.content["version"]
            work["rules_version"] = self.content["rules_version"]
        elif c["type"] == "equip":
            a["equipment"][c["slot"]] = c.get("item")
            recalculate(a, self.content["items"])
            if a["equipment"].get("armor") and "mage_armor" in a["effects"]:
                del a["effects"]["mage_armor"]
        elif c["type"] == "recover":
            a["resources"]["arcane_recovery"] -= c["slot"]
            a["slots"][str(c["slot"])] += 1
        else:
            a["prepared"] = list(c["spells"])
            if "mage_armor" not in a["prepared"]:
                for t in work["actors"].values():
                    if t["effects"].get("mage_armor", {}).get("source") == a["id"]:
                        del t["effects"]["mage_armor"]
                        t["ac"] = t["base_ac"]
        self._emit(work, "growth", self._management_preview(state, c)["description"])
        work["revision"] += 1
        return work, [deepcopy(work["event_log"][-1])]

    def _new_ability_preview(self, state, c):
        from .engine import GameError
        from . import spatial

        aid = c.get("ability")
        b = self.content["abilities"][aid]
        a = self._actor(state, c.get("actor"))
        if (
            set(c) - {"type", "actor", "ability", "target", "slot"}
            or aid not in a["abilities"]
        ):
            raise GameError("unknown_ability", "能力或参数无效。")
        if aid in ("flurry", "patient_defence") and (
            a.get("equipment", {}).get("armor")
            or a.get("equipment", {}).get("off_hand")
        ):
            raise GameError("monk_armor", "此武僧技需要无甲无盾。")
        if b["level"] and aid not in a.get("prepared", []):
            raise GameError("not_prepared", "法术尚未准备。")
        if b["cost"] != "free":
            self._need(a, b["cost"])
        cost = {} if b["cost"] == "free" else {b["cost"]: 1}
        if b.get("resource"):
            r = b["resource"]
            if a["resources"].get(r, 0) < 1:
                raise GameError("insufficient_resource", "职业资源不足。")
            cost[r] = 1
        if b["level"]:
            self._casting_equipment(a)
            slot = c.get("slot", b["level"])
            if (
                type(slot) != int
                or slot < b["level"]
                or a["slots"].get(str(slot), 0) < 1
            ):
                raise GameError("invalid_slot", "没有合法法术位。")
            cost[f"slot_{slot}"] = 1
        elif "slot" in c:
            raise GameError("invalid_slot", "该能力不使用法术位。")
        if aid in SELF:
            if "target" in c:
                raise GameError("invalid_target", "此能力无需指定目标。")
            targets = [a]
            if aid in ("mass_healing_word", "preserve_life", "aid"):
                targets = []
                for t in state["actors"].values():
                    if t["side"] == "party" and "dead" not in t["conditions"]:
                        try:
                            self._in_range(state, a, t, b["range"])
                            targets.append(t)
                        except GameError:
                            pass
        else:
            t = self._target(
                state, c.get("target"), "party" if aid in FRIENDLY else "enemy"
            )
            self._in_range(state, a, t, b["range"])
            targets = [t]
            if aid == "fireball":
                targets = [
                    x
                    for x in state["actors"].values()
                    if self._visible(state, x)
                    and x["side"] != a["side"]
                    and "dead" not in x["conditions"]
                    and spatial.distance(state["map"], t["position"], x["position"])
                    <= 6
                ]
        result = dict(
            command=deepcopy(c),
            revision=state["revision"],
            cost=cost,
            targets=[t["id"] for t in targets],
            description=b["name"] + "：" + b["description"],
            warnings=[],
        )
        if aid in ("flurry", "scorching_ray"):
            from . import dice

            adv, dis, height = self._attack_context(
                state, a, targets[0], ranged=aid == "scorching_ray"
            )
            stat = (
                ("dex" if a["stats"]["dex"] >= a["stats"]["str"] else "str")
                if aid == "flurry"
                else a["spell_stat"]
            )
            result["hit_probability"] = dice.hit_probability(
                proficiency(a) + mod(a["stats"][stat]) + height,
                targets[0]["ac"],
                adv,
                dis,
                self._blessed(a),
            )
            result["attack_count"] = 2 if aid == "flurry" else c.get("slot", 2) + 1
        elif aid == "fireball":
            result["save_dc"] = 8 + proficiency(a) + mod(a["stats"]["int"])
        return result

    def _casting_equipment(self, a):
        eq = a.get("equipment", {})
        for slot in ("armor", "off_hand"):
            item = self.content["items"].get(eq.get(slot), {})
            if item and item.get("category") not in PROF[a["template"]]:
                self._growth_error("armor_proficiency", "未熟练护甲或盾牌阻止施法。")

    def _new_effect(self, state, a, c, ids):
        from . import dice

        aid = c["ability"]
        slot = c.get("slot", self.content["abilities"][aid]["level"])
        targets = [state["actors"][x] for x in ids]
        t = targets[0]
        self._emit(
            state,
            "ability",
            a["id"] + " 使用 " + self.content["abilities"][aid]["name"],
            slot=slot,
        )
        if aid == "action_surge":
            a["budgets"]["action"] += 1
        elif aid == "patient_defence":
            if "dodge" not in a["conditions"]:
                a["conditions"].append("dodge")
        elif aid == "rally":
            if t["hp"] == 0:
                self._heal(state, t, 1, "鼓舞起身")
            t["temp_hp"] = max(t["temp_hp"], 8)
        elif aid == "preserve_life":
            for t in targets:
                self._heal(state, t, 3 * a["level"], "维持生命")
        elif aid == "aid":
            n = 5 * (slot - 1)
            for t in targets:
                delta = max(0, n - t["effects"].get("aid", {}).get("amount", 0))
                t["max_hp"] += delta
                if delta:
                    t["effects"]["aid"] = {"source": a["id"], "amount": n}
                self._heal(state, t, delta, "援助术")
        elif aid in ("mass_healing_word", "preserve_life", "aid"):
            n, detail = dice.roll(state, f"{slot-2}d4")
            for t in targets:
                self._heal(
                    state,
                    t,
                    n + mod(a["stats"]["wis"]) + 2 + slot,
                    "群体治愈真言",
                    detail,
                )
        elif aid in ("flurry", "scorching_ray"):
            for _ in range(2 if aid == "flurry" else slot + 1):
                if t["hp"] <= 0:
                    break
                self._attack(
                    state,
                    a,
                    t,
                    ("1d6" if a["level"] >= 3 else "1d4") if aid == "flurry" else "2d6",
                    "bludgeoning" if aid == "flurry" else "fire",
                    (
                        ("dex" if a["stats"]["dex"] >= a["stats"]["str"] else "str")
                        if aid == "flurry"
                        else a["spell_stat"]
                    ),
                    ranged=aid != "flurry",
                    damage_modifier=None if aid == "flurry" else 0,
                    proficiency_override=proficiency(a),
                )
        elif aid == "fireball":
            centre = state["actors"][c["target"]]
            from . import spatial

            targets = [
                x
                for x in state["actors"].values()
                if "dead" not in x["conditions"]
                and spatial.distance(state["map"], centre["position"], x["position"])
                <= 6
            ]
            n, detail = dice.roll(state, "8d6")
            dc = 8 + proficiency(a) + mod(a["stats"]["int"])
            for t in targets:
                if (
                    t["side"] == a["side"]
                    and a["template"] == "wizard"
                    and a["level"] >= 2
                ):
                    continue
                self._damage(
                    state,
                    t,
                    n // 2 if self._save(state, t, "dex", dc, "火球术") else n,
                    "fire",
                    "火球术",
                    detail,
                )
