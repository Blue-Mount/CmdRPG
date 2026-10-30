"""Six-class combat subset: shared preview/execution rules, no background actions."""

from copy import deepcopy

from . import dice, spatial
from .growth import mod


CLASS_NAMES = {'rogue': ('游荡者', '盗贼'), 'warlock': ('邪术师', '邪魔宗主')}
ABILITIES = {'eldritch_blast', 'cunning_dash', 'cunning_disengage', 'cunning_hide', 'offhand_attack', 'false_life', 'shatter'}


class ExpansionMixin:
    def _hide_observers(self, state, actor):
        return [a for a in state['actors'].values()
                if a['side'] != actor['side'] and self._alive(a) and 'stunned' not in a['conditions']
                and spatial.distance(state['map'], a['position'], actor['position']) <= 18
                and spatial.line_of_sight(state['map'], a['position'], actor['position'])]

    def _spots_rogue(self, state, observer, actor):
        if not actor.get('stealth'):
            return True
        if observer not in self._hide_observers(state, actor):
            return False
        return (spatial.distance(state['map'], observer['position'], actor['position']) <= 3
                or 10 + mod(observer['stats']['wis']) >= actor['stealth'])

    def _reveal_rogue(self, state, actor):
        if actor.pop('stealth', None) is not None:
            self._emit(state, 'effect', f"{actor['id']} 解除躲藏。")

    def _offhand_error(self, actor):
        weapon = self.content['items'][actor['weapon']]
        if not actor.get('attacked'):
            return '需要先使用攻击动作（打空也可以）。'
        if actor.get('equipment', {}).get('off_hand') != 'offhand_dagger':
            return '需要在副手槽装备副手匕首。'
        if weapon.get('range', 1.5) > 1.5 or not (weapon.get('finesse') or actor['weapon'] == 'dagger'):
            return '主手需要灵巧近战武器；短弓不能配副手攻击。'
        return ''

    def _offhand_actor(self, actor):
        trial = deepcopy(actor)
        trial['weapon'] = 'offhand_dagger'
        trial['equipment'].update(main_hand='offhand_dagger', off_hand=None)
        trial['attack_stat'] = max(('str', 'dex'), key=lambda key: trial['stats'][key])
        return trial

    def _support_turn(self, state, enemy):
        support = enemy.get('support')
        if not support or support['uses'] <= 0 or enemy['budgets']['action'] <= 0:
            return False
        targets = [a for a in state['actors'].values() if a['side'] == enemy['side'] and self._alive(a)
                   and a['max_hp'] - a['hp'] >= 8
                   and spatial.distance(state['map'], enemy['position'], a['position']) <= support['range']
                   and spatial.line_of_sight(state['map'], enemy['position'], a['position'])]
        if not targets:
            return False
        target = min(targets, key=lambda a: (a['hp'] / a['max_hp'], a['id']))
        enemy['budgets']['action'] -= 1
        support['uses'] -= 1
        enemy['hidden'] = False
        amount, detail = dice.roll(state, support['dice'])
        self._emit(state, 'ability', f"{enemy['id']} 使用战地修复，剩余{support['uses']}次。")
        self._heal(state, target, amount, '战地修复', detail, source_actor=enemy['id'])
        enemy['ended'] = True
        return True

    def _enemy_spell(self, state, enemy, target, targets):
        spell = enemy['spell']
        radius = spell.get('radius', 0)
        if radius:
            # Choose from what this enemy can currently observe, never future actions.
            target = max(targets, key=lambda a: (sum(spatial.distance(state['map'], a['position'], b['position']) <= radius for b in targets), -a['hp'], a['id']))
            victims = [a for a in sorted(state['actors'].values(), key=lambda a: a['id']) if 'dead' not in a['conditions']
                       and spatial.distance(state['map'], target['position'], a['position']) <= radius]
        else:
            victims = [target]
        name = spell.get('name', '熔火箭')
        # Do not print identities/locations of unseen units caught in a burst.
        self._emit(state, 'ability', f"{enemy['id']} 施放{name} → {target['id']}。")
        amount, detail = dice.roll(state, enemy['dice'])
        for victim in victims:
            self._save_damage(state, victim, amount, enemy['damage_type'], spell['save'], spell['dc'], name, detail, source_actor=enemy['id'])

    def _sneak_attack(self, state, actor, target):
        """Own-turn automatic Sneak Attack; reactions deliberately excluded."""
        if 'sneak_attack' not in actor.get('passives', []) or actor.get('sneak_used'):
            return 0
        weapon = self.content['items'][actor['weapon']]
        if not (weapon.get('finesse') or weapon.get('base_weapon', actor['weapon']) == 'dagger' or weapon.get('range', 1.5) > 1.5):
            return 0
        info = self._attack_roll_info(state, actor, target, weapon.get('range', 1.5) > 1.5)
        # Advantage/disadvantage cancel before applying the eligibility rule.
        if info['disadvantage'] and not info['advantage']:
            return 0
        ally = any(a['id'] != actor['id'] and a['side'] == actor['side'] and self._alive(a)
                   and 'stunned' not in a['conditions']
                   and spatial.reach_distance(state['map'], a['position'], target['position']) <= 1.5
                   and spatial.line_of_sight(state['map'], a['position'], target['position'])
                   for a in state['actors'].values())
        if (info['advantage'] and not info['disadvantage']) or ally:
            return (actor['level'] + 1) // 2
        return 0

    def _expansion_preview(self, state, command):
        from .engine import GameError
        aid = command['ability']
        actor = self._actor(state, command.get('actor'))
        ability = self.content['abilities'][aid]
        if state['phase'] != 'combat' or state['pending']:
            raise GameError('not_in_combat', '请先处理反应，并在战斗中行动。')
        if aid not in actor['abilities'] or set(command) - {'type', 'actor', 'ability', 'target', 'targets', 'slot'}:
            raise GameError('unknown_ability', '能力或参数无效。')
        spell = aid in ('eldritch_blast', 'false_life')
        if command['type'] != ('cast' if spell else 'ability'):
            raise GameError('invalid_command', '此能力的命令类型无效。')
        self._need(actor, ability['cost'])
        cost = {ability['cost']: 1}
        normalized = deepcopy(command)
        if spell:
            self._casting_equipment(actor)
        if ability['level']:
            slot = command.get('slot', self._default_slot(actor, ability['level']))
            if (type(slot) is not int or slot < ability['level'] or actor['slots'].get(str(slot), 0) < 1):
                raise GameError('invalid_slot', '没有合法法术位。')
            if aid not in actor.get('prepared', []):
                raise GameError('not_prepared', '法术尚未准备。')
            normalized['slot'] = slot
            cost[f'slot_{slot}'] = 1
        elif 'slot' in command:
            raise GameError('invalid_slot', '该能力不使用法术位。')
        result = dict(command=normalized, revision=state['revision'], cost=cost, targets=[], warnings=[],
                      description=ability['name'] + '：' + ability['description'])
        if aid == 'offhand_attack':
            reason = self._offhand_error(actor)
            if reason:
                raise GameError('offhand_unavailable', reason)
            if 'targets' in command:
                raise GameError('invalid_target', '副手攻击只接受一个target。')
            target = self._target(state, command.get('target'), 'enemy')
            self._in_range(state, actor, target, 1.5)
            trial = self._offhand_actor(actor)
            info = self._attack_roll_info(state, trial, target)
            bonus = self._weapon_proficiency(trial) + mod(trial['stats'][trial['attack_stat']])
            sneak = self._sneak_attack(state, trial, target)
            result.update(targets=[target['id']], attack_roll=info, sneak_dice=f'{sneak}d6' if sneak else None,
                          hit_probability=dice.hit_probability(bonus, target['ac'], info['advantage'], info['disadvantage'], self._blessed(actor)))
            if sneak:
                result['description'] += f' 命中追加偷袭{sneak}d6。'
            return result
        if aid == 'cunning_hide':
            if actor.get('stealth'):
                raise GameError('already_hidden', '已经躲藏，无需重复使用。')
            observers = self._hide_observers(state, actor)
            if any(spatial.distance(state['map'], a['position'], actor['position']) <= 3 for a in observers):
                raise GameError('too_close_to_hide', '敌人视线内3米内不能躲藏，请先撤离拉开距离。')
            dc = max((10 + mod(a['stats']['wis']) for a in observers), default=0)
            result.update(stealth_dc=dc, stealth_bonus=mod(actor['stats']['dex']) + actor['proficiency'])
            result['description'] += f" 本次隐匿加值{result['stealth_bonus']:+d}，察觉DC {dc}；平局被发现。"
        if aid != 'eldritch_blast':
            if 'target' in command or 'targets' in command:
                raise GameError('invalid_target', '此能力无需目标。')
            result['targets'] = [actor['id']]
            if aid == 'false_life':
                result['description'] += f" 本次获得1d4+{4 + 5 * (normalized['slot'] - 1)}临时生命，与当前取较高值。"
            return result
        count = 2 if actor['level'] >= 5 else 1
        if ('target' in command) == ('targets' in command):
            raise GameError('invalid_target', '请选择一个目标，或按每束列出目标。')
        ids = command.get('targets') if 'targets' in command else [command['target']] * count
        if not isinstance(ids, list) or len(ids) != count or any(not isinstance(x, str) for x in ids):
            raise GameError('invalid_target', f'需要恰好{count}束目标；可重复。')
        result.update(attack_count=count, allocations=[], targets=list(dict.fromkeys(ids)))
        damage_bonus = mod(actor['stats']['cha']) if 'agonizing_blast' in actor.get('passives', []) else 0
        for index, tid in enumerate(ids, 1):
            target = self._target(state, tid, 'enemy')
            self._in_range(state, actor, target, 18)
            info = self._attack_roll_info(state, actor, target, True)
            chance = dice.hit_probability(self._spell_attack(actor) + info['height_bonus'], target['ac'],
                                          info['advantage'], info['disadvantage'], self._blessed(actor))
            result['allocations'].append(dict(index=index, target=tid, damage_dice='1d10', damage_bonus=damage_bonus,
                                               hit_probability=chance, attack_roll=info, resistant='force' in target.get('resistances', [])))
        if len(set(ids)) == 1:
            result.update(hit_probability=result['allocations'][0]['hit_probability'], attack_roll=result['allocations'][0]['attack_roll'])
        result['warnings'].append('每束独立命中；已击败目标的剩余束跳过，不自动转移。')
        return result

    def _expansion_cast(self, state, actor, command):
        aid = command['ability']
        self._emit(state, 'ability', f"{actor['id']} 使用{self.content['abilities'][aid]['name']}。", slot=command.get('slot', 0))
        if aid == 'cunning_dash':
            actor['budgets']['movement'] += actor['speed']
        elif aid == 'cunning_disengage':
            if 'disengaged' not in actor['conditions']:
                actor['conditions'].append('disengaged')
        elif aid == 'cunning_hide':
            observers = self._hide_observers(state, actor)
            natural, rolls = dice.d20(state)
            score = natural + mod(actor['stats']['dex']) + actor['proficiency']
            dc = max((10 + mod(a['stats']['wis']) for a in observers), default=0)
            success = score > dc
            if success:
                actor['stealth'] = score
            self._emit(state, 'stealth', f"{actor['id']} 隐匿检定 {score} 对察觉DC {dc}：{'成功' if success else '失败'}。", rolls=rolls, total=score, dc=dc, success=success)
        elif aid == 'offhand_attack':
            trial = self._offhand_actor(actor)
            # Swap only the attacking hand; all damage, sneak and equipment triggers
            # still run on the real actor through the shared attack resolver.
            original = actor['weapon'], actor['attack_stat'], deepcopy(actor['equipment'])
            actor.update(weapon=trial['weapon'], attack_stat=trial['attack_stat'], equipment=trial['equipment'])
            try:
                self._attack(state, actor, state['actors'][command['target']], '1d4', 'piercing', actor['attack_stat'],
                             damage_modifier=min(0, mod(actor['stats'][actor['attack_stat']])), weapon_attack=True)
            finally:
                actor['weapon'], actor['attack_stat'], actor['equipment'] = original
        elif aid == 'false_life':
            amount, detail = dice.roll(state, f"1d4+{4 + 5 * (command['slot'] - 1)}")
            actor['temp_hp'] = max(actor['temp_hp'], amount)
            self._emit(state, 'effect', f"{actor['id']} 虚假生命：当前临时生命{actor['temp_hp']}；同类取高，不叠加。", **detail)
        elif aid == 'eldritch_blast':
            count = 2 if actor['level'] >= 5 else 1
            ids = command.get('targets', [command.get('target')] * count)
            extra = mod(actor['stats']['cha']) if 'agonizing_blast' in actor.get('passives', []) else 0
            for index, tid in enumerate(ids, 1):
                target = state['actors'][tid]
                if not self._alive(target):
                    self._emit(state, 'attack_skip', f"{actor['id']} 第{index}束目标已击败，跳过。")
                    continue
                self._emit(state, 'attack_segment', f"{actor['id']} 魔能爆第{index}/{count}束 → {tid}。")
                self._attack(state, actor, target, '1d10', 'force', 'cha', ranged=True, damage_modifier=extra)
