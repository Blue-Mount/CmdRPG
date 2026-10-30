"""Equipment bonuses and target-independent comparisons, shared by UI and rules."""

from copy import deepcopy

from .growth import PROF, mod, proficiency, recalculate


BONUSES = {"attack_bonus", "damage_bonus", "spell_attack", "spell_dc", "healing_bonus", "ac_bonus", "speed_bonus", "save_bonus", "ki_bonus"}


def bonus(actor, items, field):
    total = 0
    for item_id in actor.get("equipment", {}).values():
        item = items.get(item_id, {})
        if item.get('slot') == 'off_hand' and item.get('kind') == 'weapon' and field in ('attack_bonus', 'damage_bonus'):
            continue
        if field == "speed_bonus" and item.get("slot") == "main_hand" and (actor["template"] != "monk" or
                actor["equipment"].get("armor") or actor["equipment"].get("off_hand")):
            continue
        total += item.get(field, 0)
    return total


def stats(actor, items):
    weapon = items[actor["weapon"]]
    category = weapon.get("category", "martial" if actor["weapon"] == "longsword" else "simple")
    trained = actor["weapon"] == "monk_fists" or weapon.get("base_weapon", actor["weapon"]) in PROF[actor["template"]] or category in PROF[actor["template"]]
    modifier = mod(actor["stats"][actor["attack_stat"]])
    eq = actor.get("equipment", {})
    untrained_armor = any(items.get(eq.get(slot), {}).get("category") not in PROF[actor["template"]]
                          for slot in ("armor", "off_hand") if eq.get(slot) and items[eq[slot]].get('kind') != 'weapon')
    martial = actor["template"] == "monk" and not eq.get("armor") and not eq.get("off_hand") and category == 'simple' and weapon.get('range', 1.5) <= 1.5
    spell_mod = mod(actor["stats"][actor["spell_stat"]]) if actor.get("spell_stat") else 0
    return dict(damage_dice=(("1d6" if actor["level"] >= 3 else "1d4") if martial else "0d4+1")
                if actor["weapon"] == "monk_fists" else weapon["dice"],
                damage_bonus=modifier + bonus(actor, items, "damage_bonus"),
                attack_bonus=(proficiency(actor) if trained else 0) + modifier + bonus(actor, items, "attack_bonus"),
                ac=actor["ac"], speed=actor["speed"], weapon_range=weapon.get("range", 1.5), weapon_proficient=trained,
                two_handed=bool(weapon.get("two_handed")), martial_arts=martial,
                casting_allowed=not untrained_armor, attack_disadvantage=untrained_armor,
                spell_attack=proficiency(actor) + spell_mod + bonus(actor, items, "spell_attack") if actor.get("spell_stat") else None,
                spell_dc=8 + proficiency(actor) + spell_mod + bonus(actor, items, "spell_dc") if actor.get("spell_stat") else None,
                healing_bonus=bonus(actor, items, "healing_bonus"),
                save_bonus=bonus(actor, items, "save_bonus"),
                resources={k:v for k,v in actor.get('max_resources',{}).items() if k!='arcane_recovery'})


def compare(actor, item_id, slot, items):
    candidate = deepcopy(actor)
    candidate["equipment"][slot] = item_id
    warnings = []
    if items.get(candidate["equipment"].get("main_hand"), {}).get("two_handed") and candidate["equipment"].get("off_hand"):
        if slot == 'off_hand':
            candidate['equipment']['main_hand'] = None
            warnings.append('此对比假设先卸下双手主手武器；穿戴仍需先手动卸下。')
        else:
            candidate["equipment"]["off_hand"] = None
            warnings.append("此对比假设先卸下副手；双手武器不能与盾牌同时穿戴。")
    recalculate(candidate, items)
    before, after = stats(actor, items), stats(candidate, items)
    if not after["weapon_proficient"]:
        warnings.append("武器未熟练：命中不加熟练加值。")
    if not after["casting_allowed"]:
        warnings.append("护甲或盾牌未熟练：攻击劣势且禁止施法。")
    if items.get(item_id, {}).get('ki_bonus'):
        warnings.append('仅武僧的气点上限提高；穿戴不补气，胜利后或出发前自动补满。')
    if before["martial_arts"] and not after["martial_arts"]:
        warnings.append("武器或护甲配置使武艺不可用；穿甲或持盾还会失去无甲防御与无甲移动收益。")
    return dict(before=before, after=after, warnings=warnings,
                effect=items.get(item_id, {}).get("description", "无额外特效。"))
