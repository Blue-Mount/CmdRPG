"""Opt-in tactical encounters and small, explicit equipment interactions.

Rules live in the ordinary Engine so SQLite reopening requires no special host.
Only public terrain and visible actors are used by the UI and previews.
"""

from copy import deepcopy

from . import spatial


SCENES = ('foundry', 'archive', 'sanctum')
TRAITS = {'intercept': 'off_hand', 'withdraw': 'feet', 'healing_ward': 'ring'}
GEAR = {'fighter': ('off_hand', 'intercept_shield'),
        'rogue': ('feet', 'withdrawal_boots'),
        'cleric': ('ring', 'warding_ring')}


def prepare_scene(pack, scene):
    """Modify a copied campaign pack, without changing baseline enemy stats."""
    if scene not in SCENES:
        raise ValueError('战术实验只支持 foundry、archive、sanctum。')
    pack['encounter']['tactical_scene'] = scene
    descriptions = {
        'foundry': '熔流闸位于E6，邻近操作消耗一个动作。开启后H5/H6/H7立即灼烧占位者，随后每次进入受6火焰伤害；再次操作关闭并恢复原地面。敌我均受影响，南北可绕行。',
        'archive': '书库卫士护卫修复师：距离不超过3米且视线连通时，卫士可消耗反应，抵消修复师一次伤害中的6点。震慑卫士、消耗其反应或切断护卫距离/视线均可破解。',
        'sanctum': 'H3烈焰祭坛：3米内、视线连通的敌我武器命中额外2点原类型伤害。H9庇护祭坛：同范围敌我每次受伤减少3点。邻近消耗一个动作可永久摧毁祭坛；也可以保留并利用。',
    }
    pack['encounter']['description'] += '【战术实验】' + descriptions[scene]
    if scene == 'archive':
        next(e for e in pack['encounter']['enemies'] if e['id'] == 'E1')['position'] = 'L8'
    additions = {
        'foundry': [('E6', '熔流闸', descriptions[scene])],
        'archive': [('L8', '护卫阵位', descriptions[scene])],
        'sanctum': [('H3', '烈焰祭坛', '敌我武器命中+2伤害；3米、视线连通。可邻近摧毁。'),
                    ('H9', '庇护祭坛', '敌我每次伤害-3；3米、视线连通。可邻近摧毁。')],
    }
    for cell, name, description in additions[scene]:
        pack['map']['landmarks'] = [x for x in pack['map']['landmarks'] if x['position'] != cell]
        pack['map']['landmarks'].append(dict(position=cell, name=name, description=description))
    return pack


class PlaygroundMixin:
    def _has_trait(self, actor, trait):
        return any(self.content['items'].get(item, {}).get('tactical_trait') == trait
                   for item in actor.get('equipment', {}).values())

    def _init_playground(self, state):
        scene = self.content['encounter'].get('tactical_scene')
        if not scene:
            return
        objects = {}
        if scene == 'foundry':
            objects['sluice'] = dict(name='熔流闸', position='E6', active=False,
                                     cells=['H5', 'H6', 'H7'])
        elif scene == 'sanctum':
            objects = {'ember': dict(name='烈焰祭坛', position='H3', active=True),
                       'shelter': dict(name='庇护祭坛', position='H9', active=True)}
        rules = {'sluice': '开启时H5/H6/H7占位者立即受6火焰伤害，随后每次进入同样受伤；关闭恢复原地面。',
                 'ember': '3米且视线连通：敌我武器命中伤害+2；摧毁后双方失效。',
                 'shelter': '3米且视线连通：敌我每次伤害-3；摧毁后双方失效。'}
        for ident, obj in objects.items():
            obj['description'] = rules[ident]
        state['playground'] = dict(version=1, scene=scene, objects=objects,
                                   base_hazards=deepcopy(state['map']['hazards']))
        if scene == 'archive':
            state['actors']['E1']['guard_target'] = 'E4'

    def _aura(self, state, actor, name):
        obj = state.get('playground', {}).get('objects', {}).get(name)
        return bool(obj and obj['active'] and spatial.distance(state['map'], actor['position'], obj['position']) <= 3
                    and spatial.line_of_sight(state['map'], actor['position'], obj['position']))

    def _guard(self, state, target):
        return next((a for a in sorted(state['actors'].values(), key=lambda a: a['id'])
                     if a.get('guard_target') == target['id'] and self._alive(a)
                     and 'stunned' not in a['conditions'] and a['budgets']['reaction'] > 0
                     and spatial.distance(state['map'], a['position'], target['position']) <= 3
                     and spatial.line_of_sight(state['map'], a['position'], target['position'])), None)

    def _playground_notes(self, state, actor):
        notes = []
        if self._aura(state, actor, 'ember'):
            notes.append('烈焰祭坛：武器命中伤害+2')
        if self._aura(state, actor, 'shelter'):
            notes.append('庇护祭坛：每次伤害-3')
        guard = self._guard(state, actor)
        if guard and self._visible(state, guard):
            notes.append(f"{guard['id']}护卫：消耗反应抵消一次伤害中的6点")
        if actor.get('guard_target'):
            target = state['actors'].get(actor['guard_target'])
            if target and self._visible(state, target):
                notes.append(f"护卫{target['id']}：3米且视线连通时可消耗反应减伤6")
        if actor.get('healing_ward'):
            notes.append(f"守护：下次正伤害-3，至{actor['healing_ward']['source']}下回合开始")
        if actor.get('withdraw_ready'):
            notes.append('撤步已就绪：本回合可额外安全移动至多3米一次')
        return notes

    def _playground_menu(self, state, actor):
        from .engine import GameError
        entries = []
        for ident, obj in state.get('playground', {}).get('objects', {}).items():
            if ident != 'sluice' and not obj['active']:
                continue
            command = dict(type='interact', actor=actor['id'], object=ident)
            reason = ''
            try:
                self._playground_preview(state, command)
            except GameError as exc:
                reason = exc.message
            verb = ('关闭' if obj['active'] else '开启') if ident == 'sluice' else '摧毁'
            entries.append(dict(name=verb + obj['name'] + ' ' + obj['position'],
                description='相邻且视线连通，消耗一个动作。' + obj.get('description', ''), command=command,
                cost={'action': 1}, remaining={'action': actor['budgets']['action']},
                available=not reason, reason=reason, target_mode='none'))
        if self._has_trait(actor, 'withdraw'):
            ready = actor.get('withdraw_ready') and actor['id'] in self._active(state) and not state['pending']
            entries.append(dict(name='偷袭撤步', description='成功偷袭后，本回合一次至多3米的额外移动，不触发借机；危险地面照常结算。',
                command=dict(type='withdraw', actor=actor['id']), cost={}, remaining={},
                available=bool(ready), reason='' if ready else '本回合须先成功偷袭且未使用撤步', target_mode='move'))
        return entries

    def _playground_preview(self, state, command):
        from .engine import GameError as Error
        kind = command['type']
        keys = {'type', 'actor', 'object'} if kind == 'interact' else {'type', 'actor', 'destination'}
        if set(command) != keys:
            raise Error('invalid_command', '实验行动参数无效。')
        if state['phase'] != 'combat' or state['pending']:
            raise Error('not_in_combat', '请在战斗中且无待选反应时行动。')
        actor = self._actor(state, command.get('actor'))
        if kind == 'withdraw':
            if not self._has_trait(actor, 'withdraw') or not actor.get('withdraw_ready'):
                raise Error('no_withdraw', '本回合须先成功偷袭且未使用撤步。')
            # Reuse the real movement validator without spending normal movement.
            trial = deepcopy(state)
            trial_actor = trial['actors'][actor['id']]
            trial_actor['budgets']['movement'] = 3
            trial_actor['conditions'].append('disengaged')
            checked = self.preview(trial, dict(type='move', actor=actor['id'], destination=command['destination']))
            checked.update(command=deepcopy(command), cost={}, description='偷袭撤步（额外至多3米、不触发借机）：' + checked['description'])
            return checked
        ident = command.get('object')
        obj = state.get('playground', {}).get('objects', {}).get(ident) if isinstance(ident, str) else None
        if not obj or (ident != 'sluice' and not obj['active']):
            raise Error('invalid_object', '此机关不存在或已摧毁。')
        self._need(actor, 'action')
        self._in_range(state, actor, obj, 1.5)
        text = ('关闭熔流，恢复原地面。' if obj['active'] else '开启熔流：H5/H6/H7占位者立即受6点火焰伤害，之后每次进入同样受伤；敌我均受影响。') if ident == 'sluice' else f"永久摧毁{obj['name']}，移除敌我双方的对应区域效果。"
        return dict(command=deepcopy(command), revision=state['revision'], cost={'action': 1},
                    targets=[], description=text, warnings=['机关操作消耗动作；不会替代歼灭胜利条件。'])

    def _playground_action(self, state, actor, command, checked):
        if command['type'] == 'withdraw':
            actor['withdraw_ready'] = False
            movement = actor['budgets']['movement']
            already = 'disengaged' in actor['conditions']
            if not already:
                actor['conditions'].append('disengaged')
            actor['budgets']['movement'] = 3
            self._emit(state, 'equipment_trigger', f"{actor['id']} 使用偷袭撤步。", trait='withdraw')
            self._continue_move(state, actor, checked['path'][1:])
            actor['budgets']['movement'] = movement
            if not already:
                actor['conditions'] = [c for c in actor['conditions'] if c != 'disengaged']
            return
        ident = command['object']
        obj = state['playground']['objects'][ident]
        obj['active'] = not obj['active'] if ident == 'sluice' else False
        self._emit(state, 'interact', f"{actor['id']} {checked['description']}", object=ident, active=obj['active'])
        if ident == 'sluice':
            for cell in obj['cells']:
                original = state['playground']['base_hazards'].get(cell)
                if obj['active']:
                    state['map']['hazards'][cell] = dict(damage=6, description='开启的熔流')
                elif original:
                    state['map']['hazards'][cell] = deepcopy(original)
                else:
                    state['map']['hazards'].pop(cell, None)
            if obj['active']:
                for target in sorted(state['actors'].values(), key=lambda a: a['id']):
                    if 'dead' not in target['conditions'] and target['position'] in obj['cells']:
                        self._damage(state, target, 6, 'fire', '熔流开启')

    def _playground_reduce(self, state, target, amount):
        if amount <= 0:
            return amount
        if self._aura(state, target, 'shelter'):
            reduction = min(3, amount)
            amount -= reduction
            self._emit(state, 'mitigation', f"{target['id']} 庇护祭坛抵消{reduction}点伤害。", prevented=reduction)
        guard = self._guard(state, target)
        if amount > 0 and guard:
            reduction = min(6, amount)
            amount -= reduction
            guard['budgets']['reaction'] -= 1
            # Never name a hidden protector in a visible target's event.
            label = guard['id'] if self._visible(state, guard) else '护卫'
            self._emit(state, 'mitigation', f"{target['id']} 受到{label}保护，抵消{reduction}点伤害。", prevented=reduction)
        if amount > 0 and target.pop('healing_ward', None):
            reduction = min(3, amount)
            amount -= reduction
            self._emit(state, 'mitigation', f"{target['id']} 守护指环抵消{reduction}点伤害，守护消耗。", prevented=reduction)
        return amount

    def _playground_heal(self, state, target, source_actor, source):
        healer = state['actors'].get(source_actor)
        if (state['phase'] != 'combat' or not healer or healer['id'] == target['id']
                or healer['side'] != target['side'] or not self._has_trait(healer, 'healing_ward')
                or source not in ('治愈真言', '疗伤术', '群体治愈真言')):
            return
        if not target.get('healing_ward'):
            target['healing_ward'] = {'source': healer['id']}
            self._emit(state, 'equipment_trigger', f"{target['id']} 获得守护：下次正伤害减少3点，至{healer['id']}下回合开始；不叠加、不刷新。", trait='healing_ward')

    def _playground_start(self, state, actor):
        actor.pop('withdraw_ready', None)
        for member in state['actors'].values():
            if member.get('healing_ward', {}).get('source') == actor['id']:
                member.pop('healing_ward')

    def _guard_turn(self, state, enemy):
        ward = state['actors'].get(enemy.get('guard_target'))
        if not ward or not self._alive(ward):
            return False
        # A recognizable bodyguard holds its post instead of chasing the front line.
        if spatial.distance(state['map'], enemy['position'], ward['position']) <= 3:
            self._enemy_finish(state, enemy)
            return True
        return False
