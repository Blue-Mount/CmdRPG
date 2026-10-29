"""Six-class combat subset: shared preview/execution rules, no background actions."""

from copy import deepcopy

from . import dice, spatial
from .growth import mod


CLASS_NAMES = {'rogue': ('游荡者', '盗贼'), 'warlock': ('邪术师', '邪魔宗主')}
ABILITIES = {'eldritch_blast', 'cunning_dash', 'cunning_disengage', 'false_life', 'shatter'}


class ExpansionMixin:
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
