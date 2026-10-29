"""Versioned expeditions using the same combat and atomic command pipeline."""

from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path

from .content import load_content, _validate, _JSONObjectPairs, _duplicate_checked, _invalid_constant
from .growth import snapshot, recalculate


COMMANDS = {"travel", "camp", "buy"}
CONTENT_VERSION = '0.8.0-campaign'


def load_campaign(path=None):
    source = Path(path) if path else Path(__file__).resolve().parents[1] / 'content/campaign.json'
    document = source.read_text('utf-8')
    if path is not None:
        return _decode_campaign(document)
    # Database reopen and map transitions create fresh engines. Reuse only the
    # validated document, while returning an independent mutable copy each time.
    # Full source texts are cache keys: edits do not depend on timestamp accuracy.
    content_dir = source.parent
    return deepcopy(_cached_campaign(document, (content_dir/'pack.json').read_text('utf-8'),
                                     (content_dir/'growth.json').read_text('utf-8')))


@lru_cache(maxsize=2)
def _cached_campaign(document, base_document, growth_document):
    return _decode_campaign(document)


def _decode_campaign(document):
    data = _duplicate_checked(json.loads(document, object_pairs_hook=_JSONObjectPairs,
                                         parse_constant=_invalid_constant))
    if data.get('version') != 1 or not data.get('name') or data.get('start') not in data.get('nodes', {}):
        raise ValueError('campaign: invalid version or entry')
    base = load_content()
    from .growth import DATA
    items = base['items'] | DATA['items']
    if not data.get('shop') or any(k not in items or type(v) is not int or v <= 0 for k, v in data['shop'].items()):
        raise ValueError('campaign: invalid shop')
    data.setdefault('shop_unlocks', {key: 1 for key in data['shop']})
    if not isinstance(data['shop_unlocks'], dict) or set(data['shop_unlocks']) != set(data['shop']) or any(type(v) is not int or not 1 <= v <= 14 for v in data['shop_unlocks'].values()):
        raise ValueError('campaign: invalid shop unlocks')
    _validate_nodes(data['nodes'], data['start'], base, items, DATA, 14)
    _validate_nodes(data.get('previous_nodes', {}), data['start'], base, items, DATA, 7)
    _validate_nodes(data.get('legacy_nodes', {}), data['start'], base, items, DATA, 5)
    _validate_economy(data, items, DATA)
    return data


def _equipment_signature(item):
    """Compare effects as well as IDs so renaming shop gear cannot make a drop exclusive."""
    weapon = item.get('base_weapon', '')
    defaults = dict(range=1.5, finesse=weapon=='dagger', two_handed=False)
    keys = ('slot','kind','category','dice','damage_type','ac','range','finesse','two_handed',
            'attack_bonus','damage_bonus','spell_attack','spell_dc','healing_bonus','ac_bonus',
            'speed_bonus','save_bonus','ki_bonus')
    return tuple(item.get(k, defaults.get(k, 0)) for k in keys)


def _validate_economy(data, items, growth):
    types = data.get('shop_types')
    categories = data.get('shop_categories')
    required = {'weapons','armor','arcane','rings','boots','supplies'}
    if not isinstance(types, dict) or set(types)!=required or any(not isinstance(v,str) or not v.strip() for v in types.values()):
        raise ValueError('campaign: invalid shop types')
    if not isinstance(categories, dict) or set(categories)!=set(data['shop']) or any(not isinstance(v,str) or v not in required for v in categories.values()):
        raise ValueError('campaign: invalid shop categories')
    slots = dict(weapons={'main_hand'},arcane={'main_hand'},armor={'armor','off_hand'},rings={'ring'},boots={'feet'},supplies={'consumable'})
    if any(items[key]['slot'] not in slots[category] for key,category in categories.items()):
        raise ValueError('campaign: shop category does not match equipment slot')
    old_shop, old_unlocks = data.get('legacy_shop'), data.get('legacy_shop_unlocks')
    if (not isinstance(old_shop,dict) or not isinstance(old_unlocks,dict) or set(old_shop)!=set(old_unlocks)
        or not set(old_shop)<=set(data['shop']) or any(type(price) is not int or price<=0 for price in old_shop.values())
        or any(type(stage) is not int or not 1<=stage<=7 for stage in old_unlocks.values())):
        raise ValueError('campaign: invalid legacy shop')
    shop_ids = set(data['shop']) | set(old_shop)
    shop_effects = {_equipment_signature(items[item]) for item in shop_ids}
    for group in ('nodes','previous_nodes','legacy_nodes'):
        for node in data[group].values():
            for reward in node.get('equipment_reward',{}).values():
                if reward in shop_ids or _equipment_signature(items[reward]) in shop_effects:
                    raise ValueError('campaign: battle rewards must be exclusive to battle')
    # All paths share this level curve; each pre-finale win has a tangible reward.
    for node in data['nodes'].values():
        previous_level = max((n['reward_level'] for n in data['nodes'].values() if n['stage']==node['stage']-1),default=1)
        if node['stage']<14 and node['reward_level']==previous_level and set(node.get('equipment_reward',{}))!=set(growth['classes']):
            raise ValueError('campaign: missing class rewards between level milestones')
    for template in growth['classes']:
        seen = set()
        for stage in range(1,15):
            batch = {n['equipment_reward'][template] for n in data['nodes'].values()
                     if n['stage']==stage and template in n.get('equipment_reward',{})}
            if seen & batch:
                raise ValueError('campaign: repeated reward across battle stages')
            seen.update(batch)


def _validate_nodes(nodes, start, base, items, growth, total):
    if not nodes or start not in nodes:
        raise ValueError('campaign: missing entry')
    for key, node in nodes.items():
        if type(node.get('stage')) is not int or not 1 <= node['stage'] <= total:
            raise ValueError('campaign: invalid stage')
        if type(node.get('gold')) is not int or node['gold'] < 0 or not node.get('name'):
            raise ValueError('campaign: invalid reward/name')
        if type(node.get('reward_level')) is not int or not 1 <= node['reward_level'] <= 8:
            raise ValueError('campaign: invalid reward level')
        if 'equipment_reward' in node and (not isinstance(node['equipment_reward'], dict) or not node['equipment_reward'] or not set(node['equipment_reward']).issubset(growth['classes']) or
                any(not isinstance(v, str) or v not in items or items[v]['kind'] == 'potion' for v in node['equipment_reward'].values())):
            raise ValueError('campaign: invalid equipment reward')
        if not isinstance(node.get('next'), list) or len(set(node['next'])) != len(node['next']):
            raise ValueError('campaign: duplicate or invalid route')
        if any(n not in nodes or nodes[n]['stage'] != node['stage'] + 1 for n in node['next']):
            raise ValueError('campaign: routes must advance exactly one stage')
        if (node['stage'] == total) != (not node['next']):
            raise ValueError('campaign: invalid terminal stage')
        pack = deepcopy(base)
        pack.update(map=deepcopy(node['map']), encounter=deepcopy(node['encounter']), enemies=deepcopy(node['prototypes']))
        _validate(pack)  # Includes safe paths, geometry, unique spawns and enemy mechanics.
        # Reaching an enemy's occupied tile is insufficient: a melee party needs
        # a safe, visible attack position without walking through that enemy.
        from . import spatial
        board = node['map']
        for enemy in node['encounter']['enemies']:
            occupied = set(board.get('hazards', {})) | {enemy['position']}
            reachable = spatial.paths(board, node['encounter']['party_spawns'][0], occupied)
            if not any(spatial.reach_distance(board, cell, enemy['position']) <= 1.5
                       and spatial.line_of_sight(board, cell, enemy['position']) for cell in reachable):
                raise ValueError(f"campaign: {key}/{enemy['id']} has no safe melee approach")
    if nodes[start]['stage'] != 1:
        raise ValueError('campaign: entry must be level one')
    reached, pending = set(), [start]
    while pending:
        key = pending.pop()
        if key not in reached:
            reached.add(key)
            pending.extend(nodes[key]['next'])
    if reached != set(nodes):
        raise ValueError('campaign: unreachable content')


class CampaignMixin:
    def _campaign_nodes(self, state=None):
        if state is not None and state.get('campaign', {}).get('edition', 1) == 1:
            return self.campaign_content['legacy_nodes']
        if state is not None and state.get('campaign', {}).get('edition') == 2:
            return self.campaign_content['previous_nodes']
        return self.campaign_content['nodes']

    def _shop_stock(self, state):
        stage = self._campaign_nodes(state)[state['campaign']['node']]['stage']
        shop, unlocks = self._shop_tables(state)
        return {k: v for k, v in shop.items() if unlocks[k] <= stage}

    def _shop_tables(self, state):
        if state['campaign'].get('edition', 1) < 3:
            return self.campaign_content['legacy_shop'], {k: (3 if v == 5 else v) for k,v in self.campaign_content['legacy_shop_unlocks'].items()}
        return self.campaign_content['shop'], self.campaign_content['shop_unlocks']

    def _campaign_pack(self, node, state=None):
        pack = load_content()
        entry = self._campaign_nodes(state)[node]
        pack.update(map=deepcopy(entry['map']), encounter=deepcopy(entry['encounter']), enemies=deepcopy(entry['prototypes']))
        return pack

    def _new_campaign(self, name, templates, seed):
        from .engine import Engine
        start = self.campaign_content['start']
        progress = dict(version=1, edition=3, node=start, cleared=[], camped=[], gold=40, unlocked_level=1, completed=False)
        return Engine(self._campaign_pack(start)).new_run(name, templates, seed, _carry={'campaign': progress})

    def _campaign_view(self, state):
        if not state.get('campaign'):
            return None
        progress = deepcopy(state['campaign'])
        nodes = self._campaign_nodes(state)
        node = nodes[progress['node']]
        shop, unlocks = self._shop_tables(state)
        categories, types = self.campaign_content['shop_categories'], self.campaign_content['shop_types']
        progress.update(name=self.campaign_content['name'], encounter=node['name'], stage=node['stage'], total=max(n['stage'] for n in nodes.values()),
                        routes=[{'id': key, 'name': nodes[key]['name']}
                                for key in node['next']] if state['phase'] == 'victory' and not progress['completed'] else [],
                        shop=[dict(id=k, name=self.content['items'][k]['name'], price=v,
                                   description=self.content['items'][k].get('description', ''),
                                   shop_type=categories[k], shop_name=types[categories[k]], unlock_stage=unlocks[k], new=unlocks[k]==node['stage'])
                              for k, v in self._shop_stock(state).items()])
        progress['shop_types'] = [dict(id=key,name=name,count=sum(i['shop_type']==key for i in progress['shop']),
                                      new_count=sum(i['shop_type']==key and i['new'] for i in progress['shop'])) for key,name in types.items()]
        progress['next_restock'] = min((stage for stage in unlocks.values() if stage>node['stage']),default=None)
        return progress

    def _campaign_reward(self, state):
        progress = state['campaign']
        key = progress['node']
        if key in progress['cleared']:
            return
        node = self._campaign_nodes(state)[key]
        progress['cleared'].append(key)
        progress['gold'] += max(0,node['gold']-20)
        progress['unlocked_level'] = max(progress['unlocked_level'], node['reward_level'])
        for actor in state['actors'].values():
            if actor['side'] == 'party':
                previous_level = actor['level']
                snapshot(actor, progress['unlocked_level'], self.content['items'])
                if actor['level'] > previous_level:
                    for record in actor['last_growth']['records']:
                        self._emit(state, 'levelup', f"{actor['id']} {record['level']-1} → {record['level']}级：" + '；'.join(record['changes']), actor=actor['id'], level=record['level'])
        self._emit(state, 'campaign_reward', f"{node['name']}胜利：金币 +{max(0,node['gold']-20)}（原奖励{node['gold']}，已减20自动恢复）；全队{progress['unlocked_level']}级。")
        awards={}
        for actor in state['actors'].values():
            if actor['side'] == 'party':
                item = node.get('equipment_reward', {}).get(actor['template'])
                if item and item not in actor['inventory']:
                    actor['inventory'].append(item)
                    self._emit(state, 'equipment_reward', f"{actor['id']} 获得战斗奖励：{self.content['items'][item]['name']}，已收入背包；可比较后穿戴。", actor=actor['id'], item=item)
                if item and item in actor['inventory']: awards[actor['id']]=item
        progress.setdefault('equipment_awards',{})[key]=awards
        if not node['next']:
            progress['completed'] = True
            self._emit(state, 'campaign_complete', '烬王倒下，裂隙关闭。远征通关！可以保留结局或新开战役。')
        self._automatic_recovery(state, deduction=20)

    def _campaign_settlement(self, state):
        if state['phase'] != 'victory':
            return None
        progress = state['campaign']
        node = self._campaign_nodes(state)[progress['node']]
        awards=progress.get('equipment_awards',{}).get(progress['node'])
        if awards is None:
            awards={a['id']:node['equipment_reward'][a['template']] for a in state['actors'].values()
                    if a['side']=='party' and a['template'] in node.get('equipment_reward',{})
                    and node['equipment_reward'][a['template']] in a.get('inventory',[])}
        rewards=[dict(actor=aid,item=item,name=self.content['items'][item]['name']) for aid,item in sorted(awards.items())]
        equipment='、'.join(dict.fromkeys(r['name'] for r in rewards))
        return dict(completed=progress['completed'], next_encounter=list(node['next']), loot_available=False,
                    equipment_rewards=rewards,
                    loot_description=(f"本场净奖励{max(0,node['gold']-20)}金币（已减20自动恢复）；全队等级{progress['unlocked_level']}。" if state.get('automatic_recovery') else '旧结算：金币保持原记录，进入下一场前免费恢复，不重复扣费。') +
                        ('职业装备奖励：' + equipment + '（不自动穿戴）。' if equipment else '本结算没有已记录的新装备；旧关不追发新增奖励。'))

    def _campaign_preview(self, state, command):
        from .engine import GameError
        def fail(message):
            raise GameError('invalid_campaign', message)
        progress = state.get('campaign')
        if not progress or state['phase'] != 'victory' or state['pending']:
            fail('战役操作仅在战役胜利后的安全阶段开放。')
        kind = command['type']
        if kind=='camp': fail('胜利已自动全恢复，无需营地休整，也不会再次扣费。')
        fields = {'travel': {'type', 'destination'}, 'camp': {'type'}, 'buy': {'type', 'actor', 'item'}}
        if set(command) != fields[kind]:
            fail('战役命令参数无效。')
        cost, warnings = {}, []
        if kind == 'travel':
            destination = command['destination']
            nodes = self._campaign_nodes(state)
            node = nodes[progress['node']]
            if progress['completed'] or not isinstance(destination, str) or destination not in node['next']:
                fail('请选择当前路线上的下一站。')
            description = '前往' + nodes[destination]['name'] + '；携带配装与准备法术，上一场已自动恢复。'
            if self._needs_recovery(state):
                warnings.append('出发前按当前配装免费补满资源；旧结算不重复扣除金币。')
            warnings.append('进入后不能返回本场商店；可先购买和配装。')
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
            cost = {'gold': self._shop_stock(state)[item]}
            description = state['actors'][actor]['name'] + '购买' + self.content['items'][item]['name'] + '；装备收入该队员背包，穿戴请进入成长与装备。'
            if item == 'healing_potion':
                description = state['actors'][actor]['name'] + '购买治疗药水一瓶，立即增加其可用药水数量。'
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

    def _automatic_recovery(self, state, deduction=0, legacy=False):
        rows=[]
        for actor in sorted(state['actors'].values(),key=lambda a:a['id']):
            if actor['side']!='party': continue
            before=dict(hp=actor['hp'],slots=deepcopy(actor['slots']),resources=deepcopy(actor['resources']),conditions=list(actor['conditions']))
            self._drop_concentration(state,actor,'胜利自动恢复')
            self._clear_battle_effects(actor)
            actor.update(hp=actor['max_hp'],conditions=[],death_success=0,death_failure=0,slots=deepcopy(actor['max_slots']),ended=True)
            actor['max_resources'].pop('arcane_recovery',None)
            actor['resources'].pop('arcane_recovery',None)
            actor['resources'].update(actor['max_resources'])
            actor['resources']['potions']=max(2,actor['resources'].get('potions',0))
            actor['budgets']={key:0 for key in actor['budgets']}
            actor['extra_attacks']=0
            rows.append(dict(actor=actor['id'],before=before,hp=actor['hp'],slots=deepcopy(actor['slots']),resources=deepcopy(actor['resources'])))
        state['short_rests']=0
        state['automatic_recovery']=dict(version=1,deduction=deduction,legacy=legacy,members=rows)
        self._emit(state,'automatic_recovery','全队已自动全恢复：生命、法术位、职业资源；倒地/死亡复苏，药水补至至少2瓶。无需短休、营地或奥术回想。',deduction=deduction)

    def _needs_recovery(self, state):
        return not state.get('automatic_recovery') or any(
            a['hp']!=a['max_hp'] or a['conditions'] or a.get('effects') or a.get('concentration')
            or a['slots']!=a['max_slots'] or a['resources'].get('potions',0)<2
            or any(a['resources'].get(k,0)!=v for k,v in a['max_resources'].items() if k!='arcane_recovery')
            for a in state['actors'].values() if a['side']=='party')

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
            if self._needs_recovery(work):
                self._automatic_recovery(work,legacy=not bool(work.get('automatic_recovery')))
            progress['node'] = destination
            party = {}
            for aid, actor in work['actors'].items():
                if actor['side'] == 'party':
                    self._drop_concentration(work, actor, '进入下一场战斗')
                    self._clear_battle_effects(actor)
                    party[aid] = actor
            carry = {k: work[k] for k in ('id', 'campaign', 'event_log', 'event_seq', 'short_rests')}
            carry['party'] = party
            work = Engine(self._campaign_pack(destination, state)).new_run(work['name'],
                [party[f'P{i}']['template'] for i in range(1, 5)], work['rng_state'], _carry=carry)
        elif kind == 'camp':
            progress['camped'].append(progress['node'])
            work['short_rests'] = 2
            for actor in work['actors'].values():
                if actor['side'] == 'party':
                    self._drop_concentration(work, actor, '营地休整')
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
