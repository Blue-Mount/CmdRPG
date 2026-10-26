"""High-level passives and the command-driven second phase of the Ash King."""

from . import dice, spatial


class AdvancedMixin:
    def _save_damage(self, state, target, amount, damage_type, stat, dc, source, detail=None):
        success = self._save(state, target, stat, dc, source)
        evasion = stat == 'dex' and 'evasion' in target.get('passives', []) and self._alive(target) and 'stunned' not in target['conditions']
        dealt = (0 if success else amount // 2) if evasion else (amount // 2 if success else amount)
        if evasion:
            self._emit(state, 'effect', f"{target['id']} 闪避：敏捷豁免成功免伤，失败伤害减半。")
        self._damage(state, target, dealt, damage_type, source, detail)

    def _blessed_heal(self, state, actor, targets, slot):
        if 'blessed_healer' in actor.get('passives', []) and any(t['id'] != actor['id'] for t in targets):
            self._heal(state, actor, 2 + slot, '受祝福的医者')

    def _boss_transition(self, state, actor):
        config = actor.get('boss')
        if not config or actor.get('boss_phase', 1) != 1 or not 0 < actor['hp'] <= actor['max_hp'] * config['threshold']:
            return
        actor.update(boss_phase=2, range=18, role='ranged')
        actor['resistances'] = list(dict.fromkeys(actor.get('resistances', []) + ['fire']))
        self._emit(state, 'boss_phase', f"{actor['id']} 烬王进入第二阶段·裂隙化身：获得火焰抗性；行动改为{config['radius']}米内的裂隙震荡（{config['dice']}火焰，敏捷DC{config['dc']}成功减半），近处无人时施放18米裂隙火矛。现有控制与行动预算保留。", phase=2)

    def _boss_attack(self, state, actor, target):
        config = actor['boss']
        nearby = [a for a in self._enemy_targets(state, actor)
                  if spatial.distance(state['map'], actor['position'], a['position']) <= config['radius']]
        targets = nearby or [target]
        source = '裂隙震荡' if nearby else '裂隙火矛'
        self._emit(state, 'ability', f"{actor['id']} 施放{source} → " + '、'.join(a['id'] for a in targets) + '。')
        amount, detail = dice.roll(state, config['dice'])
        for member in targets:
            self._save_damage(state, member, amount, 'fire', 'dex', config['dc'], source, detail)
