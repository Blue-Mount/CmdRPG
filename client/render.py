"""Text rendering consumes only the local filtered view."""

import json
import re
import unicodedata

from core.dice import describe_d20


PHASES = {"combat": "战斗", "player": "玩家行动", "reaction": "等待反应",
          "reaction_wait": "等待反应", "victory": "胜利", "defeat": "战败",
          "complete": "已结束", "finished": "已结束", "battle": "战斗", "defeated": "战败"}
CONDITIONS = {"downed": "倒地", "dead": "死亡", "stable": "伤势稳定",
              "dodge": "闪避", "dodging": "闪避", "bless": "祝福",
              "disengage": "撤离", "disengaged": "撤离", "mage_armor": "法师护甲", "stunned": "震慑"}
COSTS = {"action": "动作", "bonus": "附赠动作", "reaction": "反应", "movement": "移动",
         "second_wind": "回气次数", "potions": "治疗药水", "short_rests": "短休次数", "ki": "气点", "action_surge":"动作如潮", "superiority":"优越骰", "channel":"引导神力", "arcane_recovery":"奥术回想"}
COSTS['gold'] = '金币'


def _number(value):
    return "%g" % value if isinstance(value, (int, float)) else str(value)


def _cost_name(key):
    if str(key).startswith("slot_"):
        return "%s环法术位" % str(key)[5:]
    return COSTS.get(key, key)


def render_cost(cost):
    """Translate the exact core cost without calculating client-side rules."""
    if not isinstance(cost, dict):
        return str(cost)
    return "、".join("%s %s%s" % (_cost_name(key), _number(value), "米" if key == "movement" else "")
                    for key, value in cost.items()) or "无"


def render_battle_report(view, detailed=True):
    report = view.get('battle_report')
    if not report:
        return '本场为旧存档，未记录分项战报；下一场战斗起提供完整统计。'
    heading = '本场战报' if view.get('phase') != 'combat' else '本场战报（进行中）'
    lines = ['%s：%s回合；敌方损失生命 %s，我方损失生命 %s；临时生命吸收 %s，实际治疗 %s；倒地 %s次，死亡 %s。' % (
        heading, report['rounds'], report['enemy_hp_lost'], report['party_hp_lost'],
        report['absorbed'], report['healing'], report['downed'], report['deaths'])]
    if detailed:
        support = report.get('support')
        if support is not None:
            lines.append('辅助收益：祝福促成命中 %s次、豁免成功 %s次；震慑阻止敌方回合 %s次、使敌方豁免自动失败 %s次。' % (
                support['bless_hits'], support['bless_saves'], support['stunned_turns'], support['automatic_failures']))
        else:
            lines.append('本场尚未记录辅助收益，下一场起提供分项统计。')
        for actor in view.get('party', []):
            row = report['members'][actor['id']]
            hit = '%s/%s' % (row['hits'], row['attacks']) if row['attacks'] else '无攻击掷骰'
            lines.append('%s %s：对敌有效伤害 %s，实际治疗 %s，损失生命 %s，临时生命吸收 %s；命中 %s，暴击 %s，倒地 %s次。' % (
                actor['id'], actor['name'], row['damage'], row['healing'], row['taken'], row['absorbed'],
                hit, row['criticals'], row['downed']))
            lines.append('  消耗：' + render_cost(row['costs']))
        lines.append('仅统计已观察到的战斗事件；伤害按实际扣血，治疗不含溢出与战后休整。命中只统计攻击掷骰，不含飞弹与豁免法术；环境伤害不归属队员输出。')
        lines.append('祝福仅在本次骰子缺少其加值就会失败时记功；震慑按实际跳过的敌方回合计数，不估算免伤。')
    return '\n'.join(lines)


def _actors(view):
    return view.get("party", []) + view.get("enemies", [])


def _actor(view, actor_id):
    return next((actor for actor in _actors(view) if actor.get("id") == actor_id), None)


def _actor_name(view, actor_id):
    actor = _actor(view or {}, actor_id)
    return "%s %s" % (actor_id, actor.get("name", "")) if actor else str(actor_id)


def _named_text(text, view):
    if not view:
        return str(text)
    original = str(text)

    def replace(match):
        actor = _actor(view, match[0])
        name = actor.get("name", "") if actor else ""
        # New core descriptions can already contain the Chinese name.
        if name and original[match.end():].lstrip().startswith(name):
            return match[0]
        return _actor_name(view, match[0])

    return re.sub(r"\b[PE][1-9][0-9]*\b", replace, original)


def _brief(description):
    return str(description).split("。", 1)[0].rstrip("；") + "。" if description else ""


def _conditions(actor):
    values = list(actor.get("conditions", [])) + list(actor.get("effects", {}))
    if isinstance(values, dict):
        values = list(values)
    return "、".join(CONDITIONS.get(str(value), str(value)) for value in values) or "正常"


def temporary_hp_text(actor):
    return "  临时生命 %s" % actor["temp_hp"] if actor.get("temp_hp", 0) > 0 else ""


def actor_line(actor, detailed=False):
    result = "%s %s  生命 %s/%s  防御 %s  位置 %s" % (
        actor.get("id", "?"), actor.get("name", "?"), actor.get("hp", "?"),
        actor.get("max_hp", "?"), actor.get("ac", "?"), actor.get("position", "未知"))
    result += temporary_hp_text(actor)
    budgets = actor.get("budgets")
    if budgets and actor.get("status") != "未轮到":
        result += "  动作 %s / 附赠 %s / 反应 %s / 移动 %s米" % (
            budgets.get("action", 0), budgets.get("bonus", 0),
            budgets.get("reaction", 0), budgets.get("movement", 0))
    if actor.get("status"):
        result += "  " + actor["status"]
    elif actor.get("ended"):
        result += "  已结束操作"
    result += "  [%s]" % _conditions(actor)
    if detailed and actor.get("slots"):
        result += "\n  法术位 " + "、".join("%s环:%s" % pair for pair in actor["slots"].items())
    if detailed and actor.get("resources"):
        names = {"potions": "药水", "second_wind": "回气", "ki": "气"}
        result += "\n  资源 " + "、".join("%s:%s" % (names.get(key, key), value)
                                      for key, value in actor["resources"].items())
    if actor.get("concentration"):
        concentration = actor["concentration"]
        label = concentration.get("name", concentration.get("ability", "专注效果")) if isinstance(concentration, dict) else str(concentration)
        result += "\n  专注：" + label
        if isinstance(concentration, dict):
            result += "（剩余 %s 回合，目标 %s）" % (concentration.get("remaining", "?"), "/".join(concentration.get("targets", [])))
    if detailed:
        result += "\n  等级 %s  速度 %s米" % (actor.get("level", "?"), actor.get("speed", "?"))
        if actor.get('resistances'):
            result += '\n  抗性：' + '、'.join({'fire':'火焰'}.get(k,k) for k in actor['resistances'])
        if actor.get('spell'):
            result += '\n  法术：%s，射程%s米，%s豁免DC%s，成功伤害减半。' % (actor['spell'].get('name','熔火箭'), actor['range'], actor['spell']['save'], actor['spell']['dc'])
            if actor['spell'].get('radius'):
                result += ' 范围%s米，敌友均受影响。' % actor['spell']['radius']
        if detailed and actor.get('attacks'):
            result += '\n  每动作攻击%s次。' % actor['attacks']
        if detailed and actor.get('support'):
            support=actor['support']
            result += '\n  战地修复：%s，范围%s米，剩余%s次；消耗动作。' % (support['dice'],support['range'],support['uses'])
        if actor.get('save_proficiencies'):
            result += '\n  豁免熟练：' + '、'.join(actor['save_proficiencies'])
        if actor.get("stats"):
            result += "\n  属性 " + " ".join("%s=%s" % pair for pair in actor["stats"].items())
    return result


def render_growth(actor):
    slots={'main_hand':'主手','off_hand':'副手','armor':'护甲','ring':'戒指','feet':'鞋子'}
    stats={'str':'力量','dex':'敏捷','con':'体质','int':'智力','wis':'感知','cha':'魅力'}
    lines=[f"{actor.get('id','')} {actor.get('name','')} · 等级 {actor.get('level',1)} · 熟练 +{actor.get('proficiency',2)} · 额外攻击 {actor.get('extra_attack',0)}"]
    recent = actor.get('last_growth')
    if recent:
        lines.append('最近成长：%s → %s级' % (recent['previous'], recent['level']))
        for record in recent['records']:
            lines.append('  %s级：%s' % (record['level'], '；'.join(record['changes'])))
    lines.append('growth %s 查看完整升级记录。' % actor.get('id', 'P1'))
    lines.append('属性：'+'，'.join(stats.get(k,k)+' '+str(v) for k,v in actor.get('stats',{}).items()))
    lines.append('装备：'+'，'.join(slots.get(k,k)+' '+v for k,v in actor.get('equipment_names',{}).items()))
    lines.extend(_combat_lines(actor))
    lines.extend('战术：' + note for note in actor.get('tactical_notes', []))
    lines.append('法术位：'+render_cost({'slot_'+k:v for k,v in actor.get('slots',{}).items()}))
    lines.append('职业资源：'+render_cost(actor.get('resources',{})))
    passives = {'evasion':'闪避：敏捷半伤豁免成功免伤，失败减半（失能时无效）',
                'blessed_healer':'受祝福的医者：法术治疗其他队友时，自身恢复2+环级（每次施法一次）',
                'sneak_attack':'偷袭：自己回合每次命中至多触发一次；需要灵巧/远程武器，优势或清醒队友贴住目标且无净劣势。伤害随等级提高；不用于借机攻击',
                'fast_hands':'快手：每回合两个附赠动作，可用于灵巧动作和药水；不增加普通动作',
                'agonizing_blast':'痛苦魔爆：魔能爆每束命中伤害加魅力修正',
                'dark_blessing':'邪魔祝福：亲自击败敌人获得等级+魅力修正临时生命；同类取高、不叠加',
                'divine_strike':'神圣打击：自己回合首次武器命中追加1d8光耀，暴击翻倍骰数；不用于借机攻击'}
    lines.extend('被动：' + passives[key] for key in actor.get('passives', []) if key in passives)
    names={x['id']:x['name'] for x in actor.get('abilities',[])}
    lines.append('已准备：'+'、'.join(names.get(x,x) for x in actor.get('prepared',[])))
    if actor.get('always_prepared'): lines.append('领域恒定准备：'+'、'.join(names.get(x,x) for x in actor['always_prepared']))
    return '\n'.join(lines)


def _combat_lines(actor):
    combat = actor.get('combat_stats')
    if not combat:
        return []
    names = {'simple':'简易武器', 'martial':'军用武器', 'dagger':'匕首', 'staff':'木杖',
             'light':'轻甲', 'medium':'中甲', 'heavy':'重甲', 'shield':'盾牌'}
    trained = combat['proficiencies']
    lines = ['战斗：AC %s · 武器命中 %+d · 每动作%s击 · 当前剩余%s击' % (
        combat['ac'], combat['attack_bonus'], combat['attacks_per_action'], combat['attacks_remaining']),
        '移动：剩余%s米 / 速度%s米' % (_number(actor.get('budgets', {}).get('movement', 0)), _number(combat['speed'])),
        '熟练：' + '、'.join(names.get(k,k) for k in trained) + '；盾牌熟练：' + ('有' if 'shield' in trained else '无')]
    if actor.get('template') == 'rogue':
        lines.append('偷袭：%sd6 · 本回合%s；附赠动作剩余%s；武器射程%s米' % ((actor['level']+1)//2, '已使用' if actor.get('sneak_used') else '可用（条件见目标预览）', actor['budgets']['bonus'], combat.get('weapon_range', 1.5)))
    if actor.get('template') == 'warlock':
        lines.append('契约法术位：胜利后自动恢复全部；魔能爆%s束，痛苦魔爆每束%+d伤害' % (2 if actor['level']>=5 else 1, (actor['stats']['cha']-10)//2 if actor['level']>=2 else 0))
    if actor.get('template') == 'monk':
        lines.append('徒手命中 %+d（不含武器专属加值）；气点 %s/%s' % (
            combat['unarmed_attack_bonus'], actor.get('resources', {}).get('ki', 0), actor.get('max_resources', {}).get('ki', 0)))
    if combat['spell_attack'] is not None:
        lines.append('法术：攻击 %+d · 豁免DC %s' % (combat['spell_attack'], combat['spell_dc']))
    if actor.get('slots'):
        lines.append('法术位：' + '、'.join('%s环 %s/%s' % (k,v,actor.get('max_slots',{}).get(k,v)) for k,v in actor['slots'].items()))
    if not combat['casting_allowed']:
        lines.append('装备限制：护甲或盾牌未熟练，攻击劣势且禁止施法。')
    bless = actor.get('effects', {}).get('bless')
    if bless:
        durations = bless.get('durations', {})
        lines.append('祝福：攻击/豁免+1d4；' + '、'.join('%s剩余%s回合' % (s,n) for s,n in durations.items()))
    concentration = actor.get('concentration')
    if concentration:
        lines.append('专注：%s，剩余%s回合' % (concentration.get('name', concentration['ability']), concentration['remaining']))
    elif actor.get('last_concentration_end'):
        ended = actor['last_concentration_end']
        lines.append('最近专注结束：%s · %s（第%s回合）' % (ended['name'], ended['reason'], ended['round']))
    return lines


def render_growth_history(view, actor_id=None):
    party = [actor for actor in view.get('party', []) if actor_id is None or actor['id'] == actor_id]
    if not party:
        raise ValueError('没有队员 %s。' % actor_id)
    lines = ['升级记录（固定职业成长；实际生命与资源消耗保留）']
    for actor in party:
        lines.append('%s %s · 当前%s级' % (actor['id'], actor['name'], actor['level']))
        records = actor.get('growth_history', [])
        if not records:
            lines.append('  暂无已保存的升级记录；旧档更新前的历史不会补造。')
        for record in records:
            lines.append('  %s → %s级：' % (record['level'] - 1, record['level']))
            lines.extend('    ' + change for change in record['changes'])
    return '\n'.join(lines)


def render_party(view):
    return "队伍：\n" + "\n".join(actor_line(actor, detailed=True)+"\n  "+render_growth(actor) for actor in view.get("party", []))


def render_event_summary(events, limit=6):
    """Keep casualties, danger and outcomes ahead of routine dice chatter."""
    important = {"victory", "defeated", "downed", "death", "reaction", "campaign_reward", "campaign_complete", "concentration_end"}
    ranked = sorted(enumerate(events), key=lambda pair: (pair[1].get("kind") in important, pair[0]), reverse=True)
    chosen = sorted(ranked[:limit])
    result = render_events([event for _, event in chosen])
    if len(events) > limit:
        result += "\n其余 %s 条已收进日志；输入 log 10 查看。" % (len(events) - limit)
    return result


def render_events(events, detailed=False):
    if not events:
        return "暂无事件。"
    lines = []
    for event in events:
        lines.append("[%s] %s" % (event.get("seq", "?"), event.get("text", "")))
        if detailed and event.get("details"):
            lines.append("  计算：" + json.dumps(event["details"], ensure_ascii=False, sort_keys=True))
    return "\n".join(lines)


def render_result(events, before=None, after=None, detailed=False):
    """Keep every acknowledged event in order, then show actual resource changes."""
    lines = ["真实结算（已执行并保存）", render_events(events, detailed=detailed)]
    interruptions = [e['text'] for e in events if e.get('kind') == 'concentration_end']
    if interruptions:
        lines.insert(1, '专注变化：' + '\n'.join(interruptions))
    old_party = {a["id"]: a for a in (before or {}).get("party", [])}
    for actor in (after or {}).get("party", []):
        old = old_party.get(actor["id"])
        if not old:
            continue
        changes = []
        for field, prefix in (("budgets", ""), ("resources", ""), ("slots", "slot_")):
            previous, current = old.get(field, {}), actor.get(field, {})
            for key in dict.fromkeys([*previous, *current]):
                if previous.get(key, 0) != current.get(key, 0):
                    changes.append("%s %s → %s%s" % (
                        _cost_name(prefix + key), _number(previous.get(key, 0)),
                        _number(current.get(key, 0)), "米" if key == "movement" else ""))
        if changes:
            lines.append("%s 实际资源变化：%s" % (_actor_name(after, actor["id"]), "；".join(changes)))
    lines.append("details 展开本次全部计算；log 10 查看历史。")
    return "\n".join(lines)


def render_actor_card(actor):
    """Actor-owned facts and static action names; never a tactical ranking."""
    budgets = actor.get("budgets", {})
    lines = ["%s %s  生命 %s/%s  [%s]" % (
        actor["id"], actor.get("name", ""), actor.get("hp", "?"), actor.get("max_hp", "?"),
        actor.get("status", "") or _conditions(actor)) + temporary_hp_text(actor),
        "动作 %s · 附赠 %s · 反应 %s · 移动 %s米" % (
            budgets.get("action", 0), budgets.get("bonus", 0), budgets.get("reaction", 0),
            _number(budgets.get("movement", 0)))]
    lines.extend(_combat_lines(actor))
    common = [entry.get("name", "") for entry in actor.get("menu_actions", [])
              if entry.get("command", {}).get("type") in ("move", "attack", "end")
              or (entry.get("command", {}).get("ability") and not any(
                  str(key).startswith("slot_") for key in entry.get("cost", {})))]
    if common:
        lines.append("常用：" + " / ".join(common))
    lines.extend('战术：' + note for note in actor.get('tactical_notes', []))
    return "\n".join(lines)


def render_action_card(view, actor_id, entry, detailed=False):
    """Explain the selected action before a target is chosen."""
    cost = "按路径消耗移动" if entry.get("target_mode") == "move" else render_cost(entry.get("cost", {}))
    lines = ["技能：" + entry.get("name", "行动"),
             "效果：" + entry.get("description", "以正式预览为准。"), "成本：" + cost]
    if entry.get("remaining"):
        lines.append("剩余：" + render_cost(entry["remaining"]))
    if detailed:
        actor = _actor(view, actor_id) or {}
        ability = next((a for a in actor.get("abilities", [])
                        if a.get("id") == entry.get("command", {}).get("ability")), {})
        lines.append("规则：" + ability.get("description", entry.get("description", "以正式预览为准。")))
        if "range" in ability:
            lines.append("射程：%s米" % _number(ability["range"]))
    else:
        lines.append("rules 展开所选技能规则；选择后由正式 preview 校验。")
    return "\n".join(lines)


def render_view(view, selected=None):
    phase = view.get("phase", "未知")
    lines = ["%s  |  %s  第 %s 回合  版本 %s" % (
        view.get("name", "远征队"), PHASES.get(phase, phase), view.get("round", "?"),
        view.get("revision", "?")),
        "当前可操作：" + ("、".join(_actor_name(view, actor_id) for actor_id in view.get("active", [])) or "无")]
    if view.get('campaign'):
        campaign = view['campaign']
        lines.insert(0, '%s · 第%s/%s站 · %s · 金币%s%s' % (
            campaign['name'], campaign['stage'], campaign['total'], campaign['encounter'], campaign['gold'],
            ' · 已通关' if campaign['completed'] else ''))
    actor = _actor(view, selected)
    if actor:
        lines.append("已选 %s %s，位于 %s；移动剩余 %s米，动作 %s，附赠动作 %s。" % (
            actor["id"], actor.get("name", ""), actor.get("position", "未知"),
            _number(actor.get("budgets", {}).get("movement", 0)),
            actor.get("budgets", {}).get("action", 0), actor.get("budgets", {}).get("bonus", 0)))
    living = [enemy for enemy in view.get("enemies", []) if enemy.get("hp", 0) > 0]
    party = view.get("party", [])
    lines.append("局势：清醒 %s 名 / 倒地 %s 名 / 死亡 %s 名；%s 名可见敌人仍在战斗。" % (
        sum(actor.get("hp", 0) > 0 for actor in party),
        sum("downed" in actor.get("conditions", []) and "dead" not in actor.get("conditions", []) for actor in party),
        sum("dead" in actor.get("conditions", []) for actor in party), len(living)))
    game_map = view.get("map", {})
    if game_map:
        lines.append(render_map(view, selected=selected, detailed=False))
    if view.get("pending"):
        pending = view["pending"]
        lines.append("待选反应：%s" % _named_text(pending.get("description", "请选择是否使用反应。"), view))
    if view.get("outcome"):
        outcomes = {"victory": "胜利", "won": "胜利", "defeat": "战败", "lost": "战败", "defeated": "战败"}
        lines.append("战斗结果：%s" % outcomes.get(view["outcome"], view["outcome"]))
        if view["outcome"] in ("defeat", "lost", "defeated"):
            lines.append("可从战前存档重试本场。")
    return "\n".join(lines)


def render_preview(number, preview, revision, view=None):
    command = preview.get("command", {})
    actor_id = command.get("actor")
    action_names = {"move": "移动", "attack": "普通攻击", "end": "结束操作", "dash": "疾走",
                    "disengage": "撤离", "dodge": "闪避", "help": "救助", "potion": "饮用治疗药水", "rest": "短休"}
    action_name = action_names.get(command.get("type"))
    if command.get("ability") and view:
        actor = _actor(view, actor_id) or {}
        ability = next((entry for entry in actor.get("abilities", []) if entry.get("id") == command["ability"]), {})
        action_name = ability.get("name", action_name)
    context = "%s | %s | 确认执行" % (_actor_name(view, actor_id), action_name or "行动") if actor_id else "确认执行"
    lines = ["%s（预览 %s，版本 %s）" % (context, number, revision),
             "影响：" + _named_text(preview.get("description", "行动"), view),
             "消耗：" + render_cost(preview.get("cost", {}))]
    if preview.get('growth_changes'):
        lines.append('升级收益：')
        lines.extend('  ' + change for change in preview['growth_changes'])
    if preview.get("targets"):
        lines.append("目标：" + "、".join(_actor_name(view, target.get("id", target.get("name", "?")))
                                         if isinstance(target, dict) else _actor_name(view, target)
                                         for target in preview["targets"]))
    if preview.get("path"):
        lines.append("路径：" + " → ".join(preview["path"]))
    if preview.get("rest_effects"):
        lines.append("全队恢复结果（一起确认）：")
        for effect in preview["rest_effects"]:
            identity = "%s %s" % (effect.get("actor", "?"), effect.get("name", ""))
            result = "  %s：生命 %s → %s（恢复 %s）" % (
                identity.strip(), effect.get("hp_before", "?"),
                effect.get("hp_after", "?"), effect.get("healing", 0))
            if effect.get("slots_restore"):
                result += "；恢复契约法术位：" + render_cost({"slot_"+k:v for k,v in effect["slots_restore"].items()})
            if effect.get("resources_restore"):
                result += "；恢复资源：" + render_cost(effect["resources_restore"])
            lines.append(result)
    if "hit_probability" in preview:
        lines.append("命中率：%.1f%%" % (100 * preview["hit_probability"]))
    if "attack_roll" in preview:
        lines.append("攻击检定：" + describe_d20(preview["attack_roll"]))
    if "failure_probability" in preview:
        lines.append("目标豁免失败概率：%.1f%%（DC %s）" % (100 * preview["failure_probability"], preview.get("save_dc", "?")))
    if "attack_count" in preview:
        lines.append("连续攻击：至多 %s 段；每段独立结算，已击败目标的剩余段跳过。" % preview["attack_count"])
    if preview.get('centre'):
        lines.append('爆心：%s；半径 %s 米；伤害 %s。' % (preview['centre'], preview['radius'], preview['damage_dice']))
    for allocation in preview.get('allocations', []):
        lines.append('第%s段 → %s：命中 %.1f%%；伤害 %s%+d%s。' % (allocation['index'], _actor_name(view, allocation['target']), 100 * allocation['hit_probability'], allocation['damage_dice'], allocation.get('damage_bonus', 0), '；目标抗性使伤害减半' if allocation.get('resistant') else ''))
        if "attack_roll" in allocation and "attack_roll" not in preview:
            lines.append('  检定：' + describe_d20(allocation["attack_roll"]))
    if preview.get('conditional_cost'):
        lines.append('条件消耗：命中时气 -1，未命中不消耗气。')
    comparison = preview.get('equipment_comparison')
    if comparison:
        lines.append('装备对比（穿戴后；命中率需在战斗中选择具体目标预览）：')
        labels = {'damage_dice':'武器伤害骰', 'damage_bonus':'武器伤害加值', 'attack_bonus':'武器命中加值',
                  'ac':'AC', 'weapon_range':'武器射程米', 'speed':'移动米', 'spell_attack':'法术攻击', 'spell_dc':'法术DC',
                  'healing_bonus':'装备法术治疗加值', 'save_bonus':'装备属性豁免加值', 'weapon_proficient':'武器熟练', 'two_handed':'双手占位',
                  'martial_arts':'无甲武艺', 'casting_allowed':'允许施法', 'attack_disadvantage':'护甲不熟练攻击劣势', 'resources':'职业资源上限'}
        changes = []
        for field, label in labels.items():
            before, after = comparison['before'][field], comparison['after'][field]
            if type(before) in (int,float) and type(after) in (int,float) and before != after:
                changes.append('%s %+g' % (label, after-before))
            elif field == 'resources':
                for key in set(before) | set(after):
                    delta = after.get(key,0) - before.get(key,0)
                    if delta: changes.append('%s上限 %+g' % (_cost_name(key), delta))
        if changes:
            lines.append('变化摘要：' + '；'.join(changes))
        for field, label in labels.items():
            before, after = comparison['before'][field], comparison['after'][field]
            if before is not None or after is not None:
                display = lambda value: ('是' if value else '否') if isinstance(value, bool) else render_cost(value) if isinstance(value, dict) else value
                delta = ('（%+g）' % (after - before)) if type(before) in (int,float) and type(after) in (int,float) and after != before else ''
                lines.append('  %s：%s → %s%s' % (label, display(before), display(after), delta))
        lines.append('特效：' + comparison['effect'])
    for save in preview.get("saves", []):
        if "failure_probability" in save:
            lines.append("%s 豁免失败概率：%.1f%%（DC %s）" % (
                _actor_name(view, save.get("target")), 100 * save["failure_probability"], save.get("dc", "?")))
            if save.get('resistant'): lines.append('  目标对此伤害有抗性：豁免结算后的伤害再减半。')
    warnings = preview.get("warnings", [])
    if warnings:
        lines.extend("危险/提示：%s" % _named_text(warning, view) for warning in warnings)
    else:
        lines.append("危险：本次合法预览没有已知危险提示。")
    lines.append("c 确认执行 | 0 返回并取消本次确认 | q 退出")
    return "\n".join(lines)


def _fallback_actions(view, actor):
    """Display legacy views; all execution still requires a legal preview."""
    reason = "请先处理待选反应" if view.get("pending") else "当前不可操作此队员" if actor["id"] not in view.get("active", []) else ""
    budgets = actor.get("budgets", {})
    entries = []
    for name, cost, description in (
        ("移动", {}, "按合法路径移动，可选择常用目的地或输入格坐标。"),
        ("普通攻击", {"action": 1}, "用武器攻击一个近邻敌人。"),
        ("疾走", {"action": 1}, "增加本回合移动距离。"),
        ("撤离", {"action": 1}, "本回合移动不触发借机攻击。"),
        ("闪避", {"action": 1}, "针对自己的攻击具有劣势，持续至下次回合开始。"),
        ("救助", {"action": 1}, "救助近邻倒地队友，恢复1生命。"),
        ("饮用治疗药水", {"bonus": 1, "potions": 1}, "治疗自己，恢复2d4+2生命。"),
        ("结束操作", {}, "结束当前角色操作，保留剩余反应。"),
    ):
        unavailable = reason
        if not unavailable and any(budgets.get(key, 0) < amount for key, amount in cost.items() if key in budgets):
            unavailable = "动作资源不足"
        if not unavailable and "potions" in cost and actor.get("resources", {}).get("potions", 0) <= 0:
            unavailable = "治疗药水已用完"
        entries.append({"name": name, "cost": cost, "description": description,
                        "available": not unavailable, "reason": unavailable,
                        "remaining": {"potions": actor.get("resources", {}).get("potions", 0)} if "potions" in cost else {}})
    for ability in actor.get("abilities", []):
        cost = {ability["cost"]: 1} if isinstance(ability.get("cost"), str) else dict(ability.get("cost", {}))
        remaining = {}
        if ability.get("level", 0):
            slot_key = "slot_%s" % ability["level"]
            cost[slot_key] = 1
            remaining[slot_key] = actor.get("slots", {}).get(str(ability["level"]), 0)
        if ability.get("id") == "second_wind":
            cost["second_wind"] = 1
            remaining["second_wind"] = actor.get("resources", {}).get("second_wind", 0)
        entries.append({**ability, "cost": cost, "remaining": remaining})
    return entries


def render_menu_actions(view, actor_id, compact=False, cards=False):
    actor = next((actor for actor in view.get("party", []) if actor.get("id") == actor_id), None)
    if actor is None:
        raise ValueError("没有队员 %s。" % actor_id)
    lines = ["%s %s | 选择行动或技能" % (actor_id, actor.get("name", "")), actor_line(actor)]
    entries = actor.get("menu_actions")
    if entries is None:
        entries = _fallback_actions(view, actor)
    if cards:
        lines = ["%s %s | 选择行动或技能" % (actor_id, actor.get("name", "")),
                 render_actor_card(actor)]
    for number, entry in enumerate(entries, 1):
        if cards:
            lines.append("%s. %s%s" % (number, entry.get("name", "行动"),
                         "" if entry.get("available") else " · 不可用：" + entry.get("reason", "条件不满足")))
            continue
        cost = render_cost(entry.get("cost", {}))
        if entry.get("target_mode") == "move" or (entry.get("name") == "移动" and not entry.get("cost")):
            cost = "按路径消耗移动"
        status = "可用" if entry.get("available") else "不可用：" + entry.get("reason", "当前条件不满足")
        line = "%s. %s | 成本：%s | %s" % (number, entry.get("name", "行动"), cost, status)
        remaining = entry.get("remaining")
        if remaining is not None and remaining != {}:
            line += " | 剩余：" + (render_cost(remaining) if isinstance(remaining, dict) else str(remaining))
        lines.append(line)
        if entry.get("description") and not compact:
            lines.append("   " + _brief(entry["description"]))
    return "\n".join(lines)


def render_rules(view, actor_id):
    actor = _actor(view, actor_id)
    if actor is None:
        raise ValueError("没有队员 %s。" % actor_id)
    entries = actor.get("menu_actions") or _fallback_actions(view, actor)
    abilities = {ability["id"]: ability for ability in actor.get("abilities", [])}
    lines = ["%s %s：技能与规则说明" % (actor_id, actor.get("name", ""))]
    for entry in entries:
        ability = abilities.get(entry.get("command", {}).get("ability"), {})
        description = ability.get("description", entry.get("description", ""))
        cost = "按路径消耗移动" if entry.get("target_mode") == "move" else render_cost(entry.get("cost", {}))
        lines.append("%s（成本：%s）：%s" % (
            entry["name"], cost, description))
    lines.append("射程、遮挡、路径和预算以服务端当前合法预览为准；预览与返回不消耗资源。")
    return "\n".join(lines)


def render_actions(view, actor_id):
    return render_menu_actions(view, actor_id)


def render_options(entries, compact=False):
    """Render core-provided legal target and landmark candidates."""
    lines = []
    for number, entry in enumerate(entries, 1):
        identity = entry.get("target", entry.get("destination", entry.get("id", "")))
        label = "%s %s" % (identity, entry.get("name", ""))
        parts = ["%s. %s" % (number, label.strip())]
        if "hp" in entry:
            parts.append("生命 %s/%s" % (entry["hp"], entry.get("max_hp", "?")))
        if entry.get("distance") is not None:
            parts.append("距离 %s米" % _number(round(entry["distance"], 2)))
        preview = entry.get("preview") or {}
        if entry.get("destination"):
            cost = entry.get("cost", preview.get("cost", {}).get("movement"))
            if cost is not None:
                parts.append("路径成本 %s米" % _number(cost) if isinstance(cost, (int, float)) else "成本 " + render_cost(cost))
        available = entry.get("available", bool(preview))
        if available:
            probability = preview.get("hit_probability", entry.get("hit_probability"))
            failure = preview.get("failure_probability", entry.get("failure_probability"))
            if probability is not None:
                parts.append("命中率 %.1f%%" % (probability * 100))
                if "attack_roll" in preview:
                    parts.append(describe_d20(preview["attack_roll"]))
            elif failure is not None:
                parts.append("豁免失败 %.1f%%" % (failure * 100))
            parts.append("本回合可达" if entry.get("destination") else "可选")
        else:
            parts.append("本回合不可达：" + entry.get("reason", "当前无法到达") if entry.get("destination")
                         else "不可用：" + entry.get("reason", "当前目标不合法"))
        lines.append(" | ".join(parts))
        if entry.get("distance_description") and not compact:
            lines.append("   距离说明：" + entry["distance_description"])
        if entry.get("description") and not compact:
            lines.append("   " + _brief(entry["description"]))
        if preview.get("warnings"):
            lines.append("   提示：" + "；".join(preview["warnings"]))
    return "\n".join(lines) or "当前没有可见候选项。"


def inspect(view, target):
    for actor in view.get("party", []) + view.get("enemies", []):
        if actor.get("id") == target:
            return actor_line(actor, detailed=True)
    if re.fullmatch(r"[PE][1-9][0-9]*", target):
        raise ValueError("没有可见角色 %s。" % target)
    match = re.fullmatch(r"([A-Z]+)([1-9][0-9]*)", target)
    if not match:
        raise ValueError("格坐标格式为 G6。")
    column = 0
    for letter in match[1]:
        column = column * 26 + ord(letter) - 64
    game_map = view.get("map", {})
    if not 1 <= column <= game_map.get("width", 0) or not 1 <= int(match[2]) <= game_map.get("height", 0):
        raise ValueError("格坐标 %s 超出地图。" % target)
    lines = ["%s：高度 %s米，%s。" % (target, game_map.get("heights", {}).get(target, 0),
              "墙体，不可通行" if target in game_map.get("walls", []) else
              "困难地面" if target in game_map.get("difficult", []) else "普通地面")]
    if target in game_map.get("hazards", {}):
        hazard = game_map["hazards"][target]
        lines.append("危险：%s，伤害 %s。" % (hazard.get("description", "危险地面"), hazard.get("damage", "?")))
    for landmark in game_map.get("landmarks", []):
        if landmark.get("position") == target:
            lines.append("%s：%s" % (landmark.get("name", ""), landmark.get("description", "")))
    known = [actor.get("id") for actor in view.get("party", []) + view.get("enemies", []) if actor.get("position") == target]
    lines.append("此格可见角色：" + ("、".join(known) or "无；不可据此判断隐藏敌人。"))
    lines.append("选择角色的“移动”，输入 %s 即可查看路径、成本和可见危险。" % target)
    return "\n".join(lines)


def _reachable_cells(reachable):
    if isinstance(reachable, dict):
        return set(reachable)
    return {entry.get("destination", entry.get("position")) if isinstance(entry, dict) else entry
            for entry in (reachable or [])}


def _wrap_map_text(line, width=78):
    """Wrap legends using display columns; grid cells remain ASCII only."""
    lines, current, columns = [], "", 0
    for character in line:
        size = 0 if unicodedata.combining(character) else 2 if unicodedata.east_asian_width(character) in ("W", "F") else 1
        if columns + size > width:
            lines.append(current)
            current, columns = "  ", 2
        current += character
        columns += size
    lines.append(current)
    return lines


def render_map(view, selected=None, reachable=None, detailed=True):
    game_map = view.get("map", {})
    width, height = game_map.get("width", 0), game_map.get("height", 0)
    if not width or not height:
        return "当前没有可见地图。"
    columns = []
    for index in range(1, width + 1):
        name, number = "", index
        while number:
            number, remainder = divmod(number - 1, 26)
            name = chr(65 + remainder) + name
        columns.append(name)
    # Dead actors remain in the filtered view for inspection and save recovery,
    # but their bodies neither occupy cells nor hide a living actor on that cell.
    living_party = [actor for actor in view.get("party", []) if "dead" not in actor.get("conditions", [])]
    living_enemies = [actor for actor in view.get("enemies", [])
                      if actor.get("hp", 0) > 0 and "dead" not in actor.get("conditions", [])]
    fallen_enemies = [actor for actor in view.get("enemies", []) if actor not in living_enemies]
    visible = {actor.get("position"): actor.get("id") for actor in living_party + living_enemies}
    reachable_cells = _reachable_cells(reachable)
    landmarks = game_map.get("landmarks", [])
    doors = {entry.get("position") for entry in landmarks if "门洞" in entry.get("name", "")}
    stairs = {entry.get("position") for entry in landmarks if any(word in entry.get("name", "") for word in ("石阶", "台阶"))}
    lines = ["文字地图：上北下南，左西右东；每格 %s米。" % _number(game_map.get("cell_size", "?")),
             "    |" + "|".join(name.center(3) for name in columns) + "|"]
    terrain_notes = []
    for ident, obj in view.get('tactical_objects', {}).items():
        status = ('开启' if obj['active'] else '关闭') if ident == 'sluice' else ('生效' if obj['active'] else '已摧毁')
        terrain_notes.append(f"{obj['position']} {obj['name']}：{status}。" + obj.get('description', ''))
    for actor in living_party + living_enemies:
        for note in actor.get('tactical_notes', []):
            terrain_notes.append(f"{actor['id']}：{note}")
    for row in range(1, height + 1):
        cells = []
        for column in columns:
            position = column + str(row)
            marker = visible.get(position)
            if marker == selected and marker is not None:
                marker = "%s*" % marker
            elif marker is None:
                if position in game_map.get("walls", []):
                    marker = "#"
                elif position in {o['position'] for o in view.get('tactical_objects', {}).values()}:
                    marker = '@'
                elif position in doors:
                    marker = "<>"
                else:
                    marker = ("!" if position in game_map.get("hazards", {}) else "")
                    marker += ":" if position in game_map.get("difficult", []) else ""
                    marker += "/" if position in stairs else ""
                    marker += "^" if game_map.get("heights", {}).get(position, 0) else ""
                    marker = marker or "."
                if position in reachable_cells and marker != "#":
                    marker = "<+>" if marker == "<>" else "+" if marker == "." else "+" + marker
            features = []
            if position in game_map.get("hazards", {}):
                features.append("危险")
            if position in game_map.get("difficult", []):
                features.append("困难地面")
            if position in stairs:
                features.append("台阶")
            altitude = game_map.get("heights", {}).get(position, 0)
            if altitude:
                features.append("高度%s米" % _number(altitude))
            if len(features) > 1 or (position in visible and features):
                terrain_notes.append("%s %s" % (position, "、".join(features)))
            # The separators disambiguate adjacent terrain cells. Column labels,
            # one-character terrain, and the center of P3* use the same anchor.
            # Current slice markers fit three cells; full compound terrain facts
            # are also listed below, including terrain covered by an actor.
            cells.append(marker[:3].center(3))
        lines.append("%3s |%s|" % (row, "|".join(cells)))
    # Repeat coordinates at the bottom so distant rows are easy to locate.
    lines.append("    |" + "|".join(name.center(3) for name in columns) + "|")
    selection_label = "%s*" % selected if selected else "P编号*"
    notes = ["图例：%s 当前选中；P1-P4 队员，E1 等为存活敌人。" % selection_label,
             "# 墙体；<> 门洞；/ 台阶；^ 高地；! 危险；: 困难；. 普通地面。"]
    if reachable is not None:
        notes.append("+ 本回合按真实路径可达；+! 危险，+: 困难，+^ 高地，<+> 门洞，+/ 台阶。")
    if terrain_notes:
        notes.append("地形补充：" + "；".join(terrain_notes))
    if view.get('tactical_objects'):
        notes.append('@ 机关/祭坛；邻近后在角色行动菜单操作。祭坛光环需3米内且视线连通。')
    party = []
    for actor in view.get("party", []):
        status = actor.get("status") or ("可操作" if actor["id"] in view.get("active", []) else "倒地" if "downed" in actor.get("conditions", []) else "死亡" if "dead" in actor.get("conditions", []) else "已结束" if actor.get("ended") else "待轮到")
        party.append("%s %s%s 生命%s/%s %s %s" % (
            actor["id"], actor.get("name", ""), "（已选）" if actor["id"] == selected else "",
            actor.get("hp", "?"), actor.get("max_hp", "?"), actor.get("position", "未知"), status) + temporary_hp_text(actor))
    if party and detailed:
        notes.append("队员：")
        notes.extend("  " + actor for actor in party)
    enemies = ["%s %s 生命%s/%s %s" % (actor["id"], actor.get("name", ""), actor.get("hp", "?"), actor.get("max_hp", "?"), actor.get("position", "未知"))
               for actor in living_enemies]
    notes.append("可见敌人：" + ("；".join(enemies) or "当前无可见敌人，敌人位置可能未知"))
    for actor in living_enemies:
        if actor.get('boss_phase'):
            rule = actor.get('boss_rule', {})
            suffix = ' · 火抗；%s米震荡/18米火矛，敏捷DC%s，%s火焰' % (rule.get('radius','?'), rule.get('dc','?'), rule.get('dice','?')) if actor['boss_phase'] == 2 else ''
            notes.append('%s Boss阶段 %s%s' % (actor['id'], actor['boss_phase'], suffix))
    for remembered in view.get("last_seen", []):
        notes.append("%s? 最后目击：第 %s 回合 %s（当前位置未知）" % (remembered["id"], remembered["round"], remembered["position"]))
    if fallen_enemies and detailed:
        notes.append("阵亡敌人：" + "；".join("%s %s %s" % (actor["id"], actor.get("name", ""), actor.get("position", "未知"))
                                             for actor in fallen_enemies))
        notes.append("遗体不阻挡移动；当前切片没有尸体掉落或搜刮。")
    if landmarks and detailed:
        notes.append("地标：" + "；".join("%s %s" % (landmark.get("position", "?"), landmark.get("name", "")) for landmark in landmarks))
        for landmark in landmarks:
            if any(word in landmark.get("name", "") for word in ("门洞", "石阶", "台阶", "瞭望台")) and landmark.get("description"):
                notes.append("  %s：%s" % (landmark["name"], _brief(landmark["description"])))
    if detailed and (game_map.get("walls") or game_map.get("heights")):
        notes.append("墙体阻挡通行和视线，高差也可能遮挡；沿门洞或石阶调整站位，目标列表会说明实际限制。")
    if game_map.get("hazards"):
        notes.append("危险位置：" + "、".join(game_map["hazards"]) + "；进入危险格可能受伤，移动预览会列出具体危险。")
    if not detailed:
        if landmarks:
            notes.append("地标：" + "；".join("%s %s" % (mark.get("position", "?"), mark.get("name", "")) for mark in landmarks))
        notes.append("map 展开地标与地形详情；inspect 格坐标 查单格；party 查资源。")
    for line in notes:
        lines.extend(_wrap_map_text(line))
    return "\n".join(lines)
