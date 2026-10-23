"""A five-battle expedition, using the same combat and atomic command pipeline."""

from copy import deepcopy
import json
from pathlib import Path

from .content import load_content, _validate, _JSONObjectPairs, _duplicate_checked, _invalid_constant
from .growth import snapshot, recalculate


COMMANDS = {"travel", "camp", "buy"}
CONTENT_VERSION = '0.4.0-m4'


def load_campaign(path=None):
    source = Path(path) if path else Path(__file__).resolve().parents[1] / 'content/campaign.json'
    data = _duplicate_checked(json.loads(source.read_text('utf-8'), object_pairs_hook=_JSONObjectPairs,
                                         parse_constant=_invalid_constant))
    if data.get('version') != 1 or not data.get('name') or data.get('start') not in data.get('nodes', {}):
        raise ValueError('campaign: invalid version or entry')
    base = load_content()
    from .growth import DATA
    items = base['items'] | DATA['items']
    if not data.get('shop') or any(k not in items or type(v) is not int or v <= 0 for k, v in data['shop'].items()):
        raise ValueError('campaign: invalid shop')
    data.setdefault('shop_unlocks', {key: 1 for key in data['shop']})
    if not isinstance(data['shop_unlocks'], dict) or set(data['shop_unlocks']) != set(data['shop']) or any(type(v) is not int or not 1 <= v <= 5 for v in data['shop_unlocks'].values()):
        raise ValueError('campaign: invalid shop unlocks')
    nodes = data['nodes']
    for key, node in nodes.items():
        if type(node.get('stage')) is not int or not 1 <= node['stage'] <= 5:
            raise ValueError('campaign: invalid stage')
        if type(node.get('gold')) is not int or node['gold'] < 0 or not node.get('name'):
            raise ValueError('campaign: invalid reward/name')
        if 'equipment_reward' in node and (not isinstance(node['equipment_reward'], dict) or set(node['equipment_reward']) != set(DATA['classes']) or
                any(not isinstance(v, str) or v not in items or items[v]['kind'] == 'potion' for v in node['equipment_reward'].values())):
            raise ValueError('campaign: invalid equipment reward')
        if not isinstance(node.get('next'), list) or len(set(node['next'])) != len(node['next']):
            raise ValueError('campaign: duplicate or invalid route')
        if any(n not in nodes or nodes[n]['stage'] != node['stage'] + 1 for n in node['next']):
            raise ValueError('campaign: routes must advance exactly one stage')
        if (node['stage'] == 5) != (not node['next']):
            raise ValueError('campaign: only stage five is terminal')
        pack = deepcopy(base)
        pack.update(map=deepcopy(node['map']), encounter=deepcopy(node['encounter']), enemies=deepcopy(node['prototypes']))
        _validate(pack)  # Includes safe paths, geometry, unique spawns and enemy mechanics.
    if nodes[data['start']]['stage'] != 1:
        raise ValueError('campaign: entry must be level one')
    reached, pending = set(), [data['start']]
    while pending:
        key = pending.pop()
        if key not in reached:
            reached.add(key)
            pending.extend(nodes[key]['next'])
    if reached != set(nodes):
        raise ValueError('campaign: unreachable content')
    return data


class CampaignMixin:
    def _shop_stock(self, state):
        stage = self.campaign_content['nodes'][state['campaign']['node']]['stage']
        return {k: v for k, v in self.campaign_content['shop'].items()
                if self.campaign_content['shop_unlocks'][k] <= stage}

    def _campaign_pack(self, node):
        pack = load_content()
        entry = self.campaign_content['nodes'][node]
        pack.update(map=deepcopy(entry['map']), encounter=deepcopy(entry['encounter']), enemies=deepcopy(entry['prototypes']))
        return pack

    def _new_campaign(self, name, templates, seed):
        from .engine import Engine
        start = self.campaign_content['start']
        progress = dict(version=1, node=start, cleared=[], camped=[], gold=40, unlocked_level=1, completed=False)
        return Engine(self._campaign_pack(start)).new_run(name, templates, seed, _carry={'campaign': progress})

    def _campaign_view(self, state):
        if not state.get('campaign'):
            return None
        progress = deepcopy(state['campaign'])
        node = self.campaign_content['nodes'][progress['node']]
        progress.update(name=self.campaign_content['name'], encounter=node['name'], stage=node['stage'], total=5,
                        routes=[{'id': key, 'name': self.campaign_content['nodes'][key]['name']}
                                for key in node['next']] if state['phase'] == 'victory' and not progress['completed'] else [],
                        shop=[dict(id=k, name=self.content['items'][k]['name'], price=v,
                                   description=self.content['items'][k].get('description', ''))
                              for k, v in self._shop_stock(state).items()])
        return progress

    def _campaign_reward(self, state):
        progress = state['campaign']
        key = progress['node']
        if key in progress['cleared']:
            return
        node = self.campaign_content['nodes'][key]
        progress['cleared'].append(key)
        progress['gold'] += node['gold']
        progress['unlocked_level'] = min(5, node['stage'] + 1)
        for actor in state['actors'].values():
            if actor['side'] == 'party':
                snapshot(actor, progress['unlocked_level'], self.content['items'])
        self._emit(state, 'campaign_reward', f"{node['name']}胜利：金币 +{node['gold']}；全队成长至{progress['unlocked_level']}级。生命与资源消耗保留，营地可恢复。")
        for actor in state['actors'].values():
            if actor['side'] == 'party':
                item = node.get('equipment_reward', {}).get(actor['template'])
                if item and item not in actor['inventory']:
                    actor['inventory'].append(item)
                    self._emit(state, 'equipment_reward', f"{actor['id']} 获得战斗奖励：{self.content['items'][item]['name']}，已收入背包；可比较后穿戴。", actor=actor['id'], item=item)
        if not node['next']:
            progress['completed'] = True
            self._emit(state, 'campaign_complete', '烬王倒下，裂隙关闭。五站远征通关！可以保留结局或新开战役。')

    def _campaign_settlement(self, state):
        if state['phase'] != 'victory':
            return None
        progress = state['campaign']
        node = self.campaign_content['nodes'][progress['node']]
        equipment = '、'.join(self.content['items'][v]['name'] for v in node.get('equipment_reward', {}).values())
        return dict(completed=progress['completed'], next_encounter=list(node['next']), loot_available=False,
                    loot_description=f"本场奖励{node['gold']}金币，已自动收入队伍钱袋；无需搜刮尸体。全队等级{progress['unlocked_level']}。" +
                        ('职业装备奖励：' + equipment + '（已拥有的不重复发放）。' if equipment else ''))

    def _campaign_preview(self, state, command):
        from .engine import GameError
        def fail(message):
            raise GameError('invalid_campaign', message)
        progress = state.get('campaign')
        if not progress or state['phase'] != 'victory' or state['pending']:
            fail('战役操作仅在战役胜利后的安全阶段开放。')
        kind = command['type']
        fields = {'travel': {'type', 'destination'}, 'camp': {'type'}, 'buy': {'type', 'actor', 'item'}}
        if set(command) != fields[kind]:
            fail('战役命令参数无效。')
        cost, warnings = {}, []
        if kind == 'travel':
            destination = command['destination']
            node = self.campaign_content['nodes'][progress['node']]
            if progress['completed'] or not isinstance(destination, str) or destination not in node['next']:
                fail('请选择当前路线上的下一站。')
            if any(a['side'] == 'party' and a['hp'] <= 0 for a in state['actors'].values()):
                fail('仍有倒地或死亡队员，请先到营地复苏，或短休救起倒地队员。')
            description = '前往' + self.campaign_content['nodes'][destination]['name'] + '；携带现有装备、准备法术、生命、药水与剩余资源。'
            warnings.append('进入后不能返回商店；检查生命与法术位，必要时先营地休整。')
        elif kind == 'camp':
            if progress['node'] in progress['camped'] or progress['completed']:
                fail('每站只能营地休整一次，通关后无需休整。')
            cost = {'gold': 20}
            description = '营地休整：全队复苏并恢复全部生命、法术位和职业资源，重置两次短休；不补药水。清除本场临时效果。'
        else:
            actor = command['actor']
            item = command['item']
            if not isinstance(actor, str) or actor not in state['actors'] or state['actors'][actor]['side'] != 'party':
                fail('请选择四名队员之一。')
            if not isinstance(item, str) or item not in self._shop_stock(state):
                fail('当前阶段商店没有此物品。')
            if item != 'healing_potion' and item in state['actors'][actor]['inventory']:
                fail('该队员已拥有此装备。')
            cost = {'gold': self.campaign_content['shop'][item]}
            description = state['actors'][actor]['name'] + '购买' + self.content['items'][item]['name'] + '；装备收入该队员背包，穿戴请进入成长与装备。'
            if item == 'healing_potion':
                description = state['actors'][actor]['name'] + '购买治疗药水一瓶，立即增加其可用药水数量。'
            if progress['node'] not in progress['camped'] and not progress['completed'] and progress['gold'] - cost['gold'] < 20:
                warnings.append('购买后将不足20金币营地休整；可自行选择留钱、短休或继续战斗。')
        if cost.get('gold', 0) > progress['gold']:
            fail('金币不足。')
        result = dict(command=deepcopy(command), revision=state['revision'], cost=cost, description=description,
                      warnings=warnings, targets=[])
        if kind == 'buy' and item != 'healing_potion':
            from .equipment import compare
            result['equipment_comparison'] = compare(state['actors'][actor], item, self.content['items'][item]['slot'], self.content['items'])
            warnings.extend(result['equipment_comparison']['warnings'])
        return result

    def _clear_battle_effects(self, actor):
        actor['max_hp'] -= actor.get('effects', {}).get('aid', {}).get('amount', 0)
        actor['hp'] = min(actor['hp'], actor['max_hp'])
        actor.update(effects={}, concentration=None, temp_hp=0)
        actor['conditions'] = [c for c in actor['conditions'] if c in ('dead', 'downed', 'stable')]
        recalculate(actor, self.content['items'])

    def _campaign_execute(self, state, command):
        checked = self._campaign_preview(state, command)
        work = deepcopy(state)
        progress = work['campaign']
        before = work['event_seq']
        progress['gold'] -= checked['cost'].get('gold', 0)
        kind = command['type']
        if kind == 'travel':
            from .engine import Engine
            destination = command['destination']
            progress['node'] = destination
            party = {}
            for aid, actor in work['actors'].items():
                if actor['side'] == 'party':
                    self._clear_battle_effects(actor)
                    party[aid] = actor
            carry = {k: work[k] for k in ('id', 'campaign', 'event_log', 'event_seq', 'short_rests')}
            carry['party'] = party
            work = Engine(self._campaign_pack(destination)).new_run(work['name'],
                [party[f'P{i}']['template'] for i in range(1, 5)], work['rng_state'], _carry=carry)
        elif kind == 'camp':
            progress['camped'].append(progress['node'])
            work['short_rests'] = 2
            for actor in work['actors'].values():
                if actor['side'] == 'party':
                    self._clear_battle_effects(actor)
                    actor.update(hp=actor['max_hp'], conditions=[], death_success=0, death_failure=0,
                                 slots=deepcopy(actor['max_slots']))
                    actor['resources'].update(actor['max_resources'])
            self._emit(work, 'camp', checked['description'])
        else:
            actor = work['actors'][command['actor']]
            if command['item'] == 'healing_potion':
                actor['resources']['potions'] += 1
            else:
                actor['inventory'].append(command['item'])
            self._emit(work, 'purchase', checked['description'])
        work['revision'] = state['revision'] + 1
        return work, [deepcopy(e) for e in work['event_log'] if e['seq'] > before]
