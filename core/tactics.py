"""Target allocation, ground bursts and one complete control/cooperation loop."""

from copy import deepcopy

from . import dice, spatial
from .growth import mod, proficiency
from .equipment import bonus


TACTICAL = {"magic_missile", "scorching_ray", "fireball", "stunning_strike"}


class TacticsMixin:
    def _tactical_preview(self, state, command):
        from .engine import GameError

        def fail(text):
            raise GameError("invalid_target", text)

        ability_id = command["ability"]
        ability = self.content["abilities"][ability_id]
        actor = self._actor(state, command.get("actor"))
        allowed = {"type", "actor", "ability", "target", "targets", "slot", "destination"}
        if ability_id == 'stunning_strike': allowed -= {'targets', 'destination'}
        if set(command) - allowed or ability_id not in actor["abilities"]:
            raise GameError("unknown_ability", "能力或参数无效。")
        expected = "ability" if ability_id == "stunning_strike" else "cast"
        if command["type"] != expected:
            raise GameError("invalid_command", "此能力的命令类型无效。")
        if state["phase"] != "combat" or state["pending"]:
            raise GameError("not_in_combat", "请在战斗中且无待选反应时行动。")
        cost = {}
        if ability_id != "stunning_strike" or not actor.get("extra_attacks", 0):
            self._need(actor, "action")
            cost["action"] = 1
        normalized = deepcopy(command)
        if ability["level"]:
            self._casting_equipment(actor)
            if actor.get("growth_version") and ability_id not in actor.get("prepared", []):
                raise GameError("not_prepared", "法术尚未准备。")
            slot = command.get("slot", ability["level"])
            if type(slot) is not int or slot < ability["level"] or actor["slots"].get(str(slot), 0) < 1:
                raise GameError("invalid_slot", "没有合法法术位。")
            normalized["slot"] = slot
            cost[f"slot_{slot}"] = 1
        else:
            if "slot" in command:
                raise GameError("invalid_slot", "震慑拳不使用法术位。")
            if actor["resources"].get("ki", 0) < 1:
                raise GameError("insufficient_resource", "至少需要1气；仅命中时消耗。")

        result = dict(command=normalized, revision=state["revision"], cost=cost,
                      targets=[], warnings=[], description=ability["name"] + "：" + ability["description"])
        if ability_id == "fireball":
            if "targets" in command or (("target" in command) == ("destination" in command)):
                fail("火球请选择一个地面落点或一个可见敌人中心。")
            if "target" in command:
                target = self._target(state, command["target"], "enemy")
                self._in_range(state, actor, target, ability["range"])
                centre = target["position"]
            else:
                centre = command["destination"]
                if not spatial.walkable(state["map"], centre):
                    fail("落点须为地图内非墙格。")
                if spatial.distance(state["map"], actor["position"], centre) > ability["range"] or not spatial.line_of_sight(state["map"], actor["position"], centre):
                    fail("落点超出射程或被遮挡。")
            targets = [t for t in self._burst_targets(state, centre)
                       if self._visible(state, t) and not self._sculpted(actor, t)]
            dc = self._spell_dc(actor)
            result.update(centre=centre, radius=6, damage_dice=f"{8 + normalized['slot'] - 3}d6", save_dc=dc,
                          targets=[t["id"] for t in targets],
                          saves=[dict(target=t["id"], dc=dc, failure_probability=self._save_failure_probability(t, "dex", dc), resistant='fire' in t.get('resistances', [])) for t in targets])
            result["warnings"].append("范围按三维距离结算；塑能保护友军，豁免成功减半，抗性另行生效。")
            return result

        if "destination" in command or (("target" in command) == ("targets" in command)):
            fail("请选择 target，或按每段攻击顺序给出 targets 列表。")
        count = 1 if ability_id == "stunning_strike" else normalized["slot"] + (2 if ability_id == "magic_missile" else 1)
        ids = command.get("targets") if "targets" in command else [command["target"]] * count
        if not isinstance(ids, list) or len(ids) != count or any(not isinstance(x, str) for x in ids):
            fail(f"需要恰好{count}段目标，可重复；单个 target 会分配全部攻击。")
        targets = [self._target(state, tid, "enemy") for tid in ids]
        for target in targets:
            self._in_range(state, actor, target, ability["range"])
        result.update(targets=list(dict.fromkeys(ids)), attack_count=count, allocations=[])
        for index, target in enumerate(targets, 1):
            roll_info = None
            if ability_id == "magic_missile":
                probability = 1.0
                expression = "1d4+1"
            else:
                ranged = ability_id == "scorching_ray"
                adv, dis, height = self._attack_context(state, actor, target, ranged)
                roll_info = self._attack_roll_info(state, actor, target, ranged)
                stat = actor["spell_stat"] if ranged else actor["attack_stat"]
                attack_bonus = self._spell_attack(actor) if ranged else self._weapon_proficiency(actor) + mod(actor["stats"][stat]) + bonus(actor, self.content["items"], "attack_bonus")
                probability = dice.hit_probability(attack_bonus + height, target["ac"], adv, dis, self._blessed(actor))
                expression = "2d6" if ranged else self.content["items"][actor["weapon"]]["dice"]
                if not ranged and actor["weapon"] == "monk_fists":
                    expression = self._unarmed_dice(actor)
            damage_bonus = mod(actor['stats'][actor['attack_stat']]) + bonus(actor, self.content['items'], 'damage_bonus') if ability_id == 'stunning_strike' else 0
            damage_type = 'force' if ability_id == 'magic_missile' else 'fire' if ability_id == 'scorching_ray' else self.content['items'][actor['weapon']]['damage_type']
            result["allocations"].append(dict(index=index, target=target["id"], hit_probability=probability, damage_dice=expression, damage_bonus=damage_bonus,
                                              resistant=damage_type in target.get('resistances', [])))
            if roll_info is not None:
                result["allocations"][-1]["attack_roll"] = roll_info
        if len(set(ids)) == 1:
            result["hit_probability"] = result["allocations"][0]["hit_probability"]
            if "attack_roll" in result["allocations"][0]:
                result["attack_roll"] = deepcopy(result["allocations"][0]["attack_roll"])
        if ability_id == "stunning_strike":
            dc = 8 + proficiency(actor) + mod(actor["stats"]["wis"])
            result.update(save_dc=dc, failure_probability=self._save_failure_probability(targets[0], "con", dc), conditional_cost={"ki_on_hit": 1})
        else:
            result["warnings"].append("逐段按已分配目标及当时状态结算；抗性变化影响后续段伤害。被击败目标的剩余段跳过，不自动转移，其他目标继续。")
        return result

    def _spell_attack(self, actor):
        return proficiency(actor) + mod(actor["stats"][actor["spell_stat"]]) + bonus(actor, self.content["items"], "spell_attack")

    def _spell_dc(self, actor):
        return 8 + proficiency(actor) + mod(actor["stats"][actor["spell_stat"]]) + bonus(actor, self.content["items"], "spell_dc")

    def _burst_targets(self, state, centre):
        return [t for t in state["actors"].values() if "dead" not in t["conditions"]
                and spatial.distance(state["map"], centre, t["position"]) <= 6]

    def _sculpted(self, actor, target):
        return actor["side"] == target["side"] and actor["template"] == "wizard" and actor["level"] >= 2

    def _tactical_cast(self, state, actor, command):
        ability_id = command["ability"]
        self._emit(state, "ability", f"{actor['id']} 使用 {self.content['abilities'][ability_id]['name']}。", slot=command.get("slot", 0))
        if ability_id == "fireball":
            centre = command.get("destination") or state["actors"][command["target"]]["position"]
            amount, detail = dice.roll(state, f"{8 + command['slot'] - 3}d6")
            for target in self._burst_targets(state, centre):
                if not self._sculpted(actor, target):
                    self._save_damage(state, target, amount, 'fire', 'dex', self._spell_dc(actor), '火球术', detail, source_actor=actor['id'])
            return
        count = 1 if ability_id == "stunning_strike" else command["slot"] + (2 if ability_id == "magic_missile" else 1)
        ids = command.get("targets", [command.get("target")] * count)
        for index, tid in enumerate(ids, 1):
            target = state["actors"][tid]
            if target["hp"] <= 0:
                self._emit(state, "attack_skip", f"{actor['id']} 第{index}段 → {tid}：目标已被击败，跳过。", index=index, target=tid)
                continue
            self._emit(state, "attack_segment", f"{actor['id']} 第{index}/{count}段 → {tid}。", index=index, target=tid)
            if ability_id == "magic_missile":
                amount, detail = dice.roll(state, "1d4+1")
                self._damage(state, target, amount, "force", f"魔法飞弹 {index}", detail, source_actor=actor['id'])
            elif ability_id == "scorching_ray":
                self._attack(state, actor, target, "2d6", "fire", actor["spell_stat"], ranged=True, damage_modifier=0)
            else:
                actor["attacked"] = True
                if actor.get("extra_attacks", 0):
                    actor["extra_attacks"] -= 1
                else:
                    actor["extra_attacks"] = actor.get("extra_attack", 0)
                hit = self._weapon_attack(state, actor, target)
                if hit:
                    actor["resources"]["ki"] -= 1
                    self._emit(state, "resource", f"{actor['id']} 震慑拳命中：气 -1，剩余 {actor['resources']['ki']}。", cost={'ki': 1})
                    dc = 8 + proficiency(actor) + mod(actor["stats"]["wis"])
                    if target["hp"] > 0 and not self._save(state, target, "con", dc, "震慑拳"):
                        target["effects"].setdefault("stunned", {})[actor["id"]] = state["round"] + 1
                        if "stunned" not in target["conditions"]:
                            target["conditions"].append("stunned")
                        target["budgets"] = {key: 0 for key in target["budgets"]}
                        target["ended"] = True
                        self._drop_concentration(state, target, "震慑失能")
                        self._emit(state, "effect", f"{tid} 被震慑，至施术者下次回合结束：无法行动、移动或反应；来袭攻击优势，力量/敏捷豁免自动失败。")

    def _expire_control(self, state, source):
        for target in state["actors"].values():
            effect = target["effects"].get("stunned", {})
            if source["id"] in effect and state['round'] >= effect[source["id"]]:
                del effect[source["id"]]
                if not effect:
                    del target["effects"]["stunned"]
                    target["conditions"] = [c for c in target["conditions"] if c != "stunned"]
                    self._emit(state, "effect_end", f"{target['id']} 震慑结束。")
