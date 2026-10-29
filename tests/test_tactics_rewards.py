"""A/B acceptance through legal commands, actual rolls, persistence and Chinese UI."""

from copy import deepcopy
import json
import os
import subprocess
import sys
from unittest.mock import patch

import pytest

from core.engine import Engine, GameError
from core.growth import DATA, recalculate, validate_growth
from core.equipment import compare, stats
from client.cli import Client
from client.parser import parse_line
from client.transport import LocalTransport
from saves.storage import Repository
from tools.dev_play import PlaygroundEngine


def arena():
    engine = Engine()
    state = engine.new_run(seed=42, levels=[5] * 4)
    state['map'].update(walls=[], heights={}, hazards={})
    for i, actor in enumerate(state['actors'].values()):
        actor.update(position=['B2','C2','D2','E2','C3','F3','G3'][i], ended=False)
        actor['budgets'] = dict(action=1, bonus=1, reaction=1, movement=actor['speed'])
        if actor['side'] == 'enemy': actor.update(hp=1000, max_hp=1000, ac=10)
    state.update(active_group=['P1','P2','P3','P4'], cursor=4,
                 turn_order=['P1','P2','P3','P4','E1','E2','E3'])
    return engine, state


def safe(state):
    state.update(phase='victory', outcome='victory', active_group=[])


def equip(engine, state, actor, item, slot='main_hand'):
    safe(state)
    return engine.execute(state, dict(type='equip', actor=actor, slot=slot, item=item))[0]


def combat(state):
    state.update(phase='combat', outcome=None, active_group=['P1','P2','P3','P4'])


@pytest.mark.parametrize('ability,slot,count,damage', [('magic_missile',1,3,15), ('magic_missile',3,5,25), ('scorching_ray',2,3,36), ('scorching_ray',3,4,48)])
def test_allocations_pure_and_execute_each_segment_once(ability, slot, count, damage):
    e, s = arena()
    ids = ['E1'] * (count - 1) + ['E2']
    c = dict(type='cast', actor='P3', ability=ability, slot=slot, targets=ids)
    before = deepcopy(s)
    p = e.preview(s, c)
    assert s == before and p['attack_count'] == count
    assert [a['target'] for a in p['allocations']] == ids
    with patch('core.dice.die', side_effect=lambda state, sides: min(10, sides)):
        after, events = e.execute(s, c)
    assert sum(1000 - after['actors'][a]['hp'] for a in ('E1','E2')) == damage
    assert after['actors']['P3']['slots'][str(slot)] == s['actors']['P3']['slots'][str(slot)] - 1
    assert len([x for x in events if x['kind'] == 'attack_segment']) == count
    assert after['actors']['P3']['budgets']['action'] == 0


@pytest.mark.parametrize('ability,slot', [('magic_missile',1), ('scorching_ray',2)])
def test_kill_skips_only_allocated_dead_target_and_continues_others(ability, slot):
    e, s = arena()
    s['actors']['E1']['hp'] = 1
    c = dict(type='cast', actor='P3', ability=ability, slot=slot, targets=['E1','E1','E2'])
    with patch('core.dice.die', side_effect=lambda state, sides: min(10, sides)):
        after, events = e.execute(s, c)
    assert after['actors']['E1']['hp'] == 0 and after['actors']['E2']['hp'] < 1000
    assert len([x for x in events if x['kind'] == 'attack_skip']) == 1


@pytest.mark.parametrize('change', [dict(targets=[]), dict(targets=['E1']), dict(targets=['E1','E2',[]]),
                                   dict(target='E1', targets=['E1']*3), dict(destination='C3'), dict(extra=1)])
def test_bad_allocation_is_atomic(change):
    e, s = arena()
    c = dict(type='cast',actor='P3',ability='magic_missile',targets=['E1']*3) | change
    before = deepcopy(s)
    with pytest.raises(GameError): e.execute(s,c)
    assert s == before


def test_ground_burst_hidden_damage_no_identity_or_count_leak_and_empty_ground():
    e,s = arena()
    s['actors']['E2'].update(position='E6', hidden=True)
    command = dict(type='cast', actor='P3', ability='fireball', destination='C3')
    before = deepcopy(s)
    p = e.preview(s,command)
    assert s == before and p['centre'] == 'C3' and 'E2' not in p['targets']
    with patch('core.dice.die', side_effect=lambda state,sides: min(10,sides)):
        after,events = e.execute(s,command)
    assert after['actors']['E2']['hp'] < 1000
    assert all(after['actors'][f'P{i}']['hp'] == s['actors'][f'P{i}']['hp'] for i in range(1,5))
    assert 'E2' not in json.dumps(events) and 'E6' not in json.dumps(events)
    assert e.preview(s,command | dict(destination='A10'))['targets'] == []
    after,_ = e.execute(s,command | dict(destination='A10'))
    assert after['actors']['P3']['slots']['3'] == 1


@pytest.mark.parametrize('destination', ['Z99',[], 'B0'])
def test_ground_invalid_atomic(destination):
    e,s=arena(); before=deepcopy(s)
    with pytest.raises(GameError): e.execute(s,dict(type='cast',actor='P3',ability='fireball',destination=destination))
    assert s == before


def test_stun_hit_extra_attack_cooperation_save_skip_expiry_and_cost():
    e,s=arena()
    c=dict(type='ability',actor='P2',ability='stunning_strike',target='E1')
    original_ki=s['actors']['P2']['resources']['ki']
    with patch('core.dice.die',side_effect=lambda state,sides:min(10,sides)):
        s,events=e.execute(s,c)
    target=s['actors']['E1']
    assert 'stunned' in target['conditions'] and all(v == 0 for v in target['budgets'].values())
    assert s['actors']['P2']['extra_attacks'] == 1
    assert s['actors']['P2']['resources']['ki'] == original_ki - 1
    assert e.preview(s,c)['cost'] == {}  # extra attack can itself be a stunning strike
    assert next(a for a in e.menu_actions(s,'P2') if a['command'].get('ability')=='stunning_strike')['available']
    e._start_actor(s,target)
    assert target['ended'] and target['budgets']['action']==0 and target['budgets']['reaction']==0
    assert e._attack_context(s,s['actors']['P1'],target)[0]
    assert e._save_failure_probability(target,'dex',1)==1
    before_rng=s['rng_state']
    assert not e._save(s,target,'dex',1,'测试') and s['rng_state']==before_rng
    e._expire_control(s,s['actors']['P2'])
    assert 'stunned' in target['conditions']
    s['round']=2
    e._start_actor(s,s['actors']['P2'])
    s,_=e.execute(s,dict(type='end',actor='P2'))
    assert 'stunned' not in s['actors']['E1']['conditions']


def test_stun_miss_does_not_spend_ki_and_successful_save_resists():
    e,s=arena(); c=dict(type='ability',actor='P2',ability='stunning_strike',target='E1')
    with patch('core.dice.die',return_value=1): after,_=e.execute(s,c)
    assert after['actors']['P2']['resources']['ki']==s['actors']['P2']['resources']['ki']
    with patch('core.dice.die',side_effect=lambda state,sides:sides): after,_=e.execute(s,c)
    assert 'stunned' not in after['actors']['E1']['conditions']
    assert after['actors']['P2']['resources']['ki']==s['actors']['P2']['resources']['ki']-1


@pytest.mark.parametrize('actor,item,field,difference', [
    ('P1','longsword_plus1','attack_bonus',1),('P1','longsword_plus1','damage_bonus',1),
    ('P2','striking_staff','attack_bonus',1),('P2','wind_staff','speed',3),
    ('P3','focus_staff','spell_attack',1),('P3','focus_staff','spell_dc',1),
    ('P3','warding_staff','ac',1),('P4','mercy_mace','healing_bonus',2),
    ('P1','gatewarden_blade','attack_bonus',1),('P1','gatewarden_blade','damage_bonus',1),
    ('P1','gatewarden_blade','ac',1),
    ('P2','stormstride_staff','attack_bonus',1),('P2','stormstride_staff','speed',3),
    ('P3','runic_bulwark_staff','spell_attack',1),('P3','runic_bulwark_staff','spell_dc',1),
    ('P3','runic_bulwark_staff','ac',1),
    ('P4','dawnwatch_mace','healing_bonus',3),('P4','dawnwatch_mace','ac',1)])
def test_each_class_equipment_direction_changes_actual_stats(actor,item,field,difference):
    e,s=arena(); before=stats(s['actors'][actor],e.content['items'])
    s=equip(e,s,actor,item)
    after=stats(s['actors'][actor],e.content['items'])
    assert after[field]==before[field]+difference and after['weapon_proficient']
    for _ in range(3): recalculate(s['actors'][actor],e.content['items'])
    assert stats(s['actors'][actor],e.content['items'])==after


@pytest.mark.parametrize('item', ['focus_staff', 'runic_bulwark_staff'])
def test_focus_preview_and_execution_attack_and_save_match(item):
    e,s=arena(); s=equip(e,s,'P3',item); combat(s)
    c=dict(type='cast',actor='P3',ability='scorching_ray',target='E2')
    p=e.preview(s,c)
    with patch('core.dice.die',side_effect=lambda state,sides:min(10,sides)):
        after,events=e.execute(s,c)
    attack=next(x['details'] for x in events if x['kind']=='attack')
    assert attack['equipment_bonus']==1 and attack['disadvantage'] and p['hit_probability']==pytest.approx(0.95 ** 2)
    assert e.preview(s,dict(type='cast',actor='P3',ability='fireball',destination='C3'))['save_dc']==16


def test_weapon_bonus_applies_reactions_but_not_flurry_and_critical_only_doubles_dice():
    e,s=arena(); s=equip(e,s,'P2','striking_staff'); combat(s)
    with patch('core.dice.die',side_effect=lambda state,sides:sides):
        e._weapon_attack(s,s['actors']['P2'],s['actors']['E1'],reaction=True)
    attack=next(x for x in reversed(s['event_log']) if x['kind']=='attack')
    damage=next(x for x in reversed(s['event_log']) if x['kind']=='damage')
    assert attack['details']['equipment_bonus']==1 and attack['details']['critical']
    assert damage['details']['raw_damage']==17  # 2d6 + dex4 + item1
    with patch('core.dice.die',side_effect=lambda state,sides:min(10,sides)):
        _,events=e.execute(s,dict(type='ability',actor='P2',ability='flurry',target='E1'))
    assert all(x['details']['equipment_bonus']==0 for x in events if x['kind']=='attack')


def test_ward_stacks_mage_armor_speed_removed_when_armored_and_comparison_warns():
    e,s=arena(); s=equip(e,s,'P3','warding_staff'); combat(s)
    s,_=e.execute(s,dict(type='cast',actor='P3',ability='mage_armor',target='P3'))
    assert s['actors']['P3']['ac']==16
    s=equip(e,s,'P2','wind_staff'); assert s['actors']['P2']['speed']==15
    s=equip(e,s,'P2','leather','armor'); assert s['actors']['P2']['speed']==9
    cmp=compare(s['actors']['P3'],'plate','armor',e.content['items'])
    assert not cmp['after']['casting_allowed'] and cmp['warnings']


@pytest.mark.parametrize('item,healing', [('mercy_mace', 2), ('dawnwatch_mace', 3)])
@pytest.mark.parametrize('ability', ['healing_word','cure_wounds','mass_healing_word'])
def test_mercy_equipment_heals_each_target_but_not_potions(ability, item, healing):
    e,s=arena(); s=equip(e,s,'P4',item); combat(s)
    for a in s['actors'].values():
        if a['side']=='party': a['hp']=1
    c=dict(type='cast',actor='P4',ability=ability)
    if ability!='mass_healing_word': c['target']='P3'
    with patch('core.dice.die',side_effect=lambda state,sides:min(10,sides)):
        after,events=e.execute(s,c)
    expected=4+4+3+healing if ability=='healing_word' else 8+4+3+healing if ability=='cure_wounds' else 4+4+5+healing
    assert after['actors']['P3']['hp']==1+expected
    s['actors']['P4']['hp']=1
    with patch('core.dice.die',side_effect=lambda state,sides:min(10,sides)):
        after,_=e.execute(s,dict(type='potion',actor='P4'))
    assert after['actors']['P4']['hp']==11


def victory():
    e=Engine(); s=e.new_run(seed=42,campaign=True)
    for a in s['actors'].values():
        if a['side']=='enemy': a.update(hp=0,conditions=['dead'])
    e._outcome(s)
    return e,s


def test_staged_shop_purchase_preview_optional_camp_and_equipment_reward_idempotence():
    e,s=victory()
    stock={x['id'] for x in e.view(s)['campaign']['shop']}
    assert {'greatsword','rapier','shortbow','swift_boots'}<=stock
    assert 'focus_staff' not in stock
    assert 'plate' not in stock and 'bastion_shield' not in stock
    with pytest.raises(GameError): e.preview(s,dict(type='buy',actor='P1',item='plate'))
    s['campaign']['node']='road'
    stock={x['id'] for x in e.view(s)['campaign']['shop']}
    assert {'wind_staff','striking_staff','focus_staff','warding_staff','mercy_mace','longsword_plus1'}<=stock
    s['campaign']['gold']=55
    p=e.preview(s,dict(type='buy',actor='P1',item='longsword_plus1'))
    assert p['equipment_comparison']['after']['attack_bonus']==p['equipment_comparison']['before']['attack_bonus']+1
    assert not any('营地' in warning for warning in p['warnings'])
    s,_=e.execute(s,p['command']); assert s['campaign']['gold']==0
    # Isolate the settlement of an actual configured key encounter.
    s['campaign'].update(node='gate',cleared=['outpost','road'])
    e._campaign_reward(s)
    before=deepcopy(s); e._campaign_reward(s); assert s==before
    rewards = e.campaign_content['nodes']['gate']['equipment_reward']
    assert set(rewards.values()).isdisjoint(e.campaign_content['shop'])
    assert len(set(rewards.values())) == 6
    for actor in s['actors'].values():
        if actor['side'] == 'party':
            item = rewards[actor['template']]
            assert actor['inventory'].count(item) == 1
            assert item not in actor['equipment'].values()
            assert any(event['kind'] == 'equipment_reward' and event['details']['item'] == item
                       for event in s['event_log'])
    assert 'plate' in {x['id'] for x in e.view(s)['campaign']['shop']}


def test_unique_rewards_equip_persist_and_carry_to_next_encounter(tmp_path):
    e, s = victory()
    s['campaign'].update(node='gate', cleared=['outpost', 'road'])
    e._campaign_reward(s)
    rewards = e.campaign_content['nodes']['gate']['equipment_reward']

    class Fixture(Engine):
        def new_run(self, *args, **kwargs):
            return deepcopy(s)

    path = tmp_path / 'unique-rewards.sqlite3'
    store = Repository(path, Fixture())
    rid = store.create_run(command_id='new')['run_id']
    for index in range(1, 5):
        aid = 'P%s' % index
        item = rewards[s['actors'][aid]['template']]
        command = dict(type='equip', actor=aid, slot='main_hand', item=item)
        preview = store.preview(rid, command)
        assert preview['equipment_comparison']['effect'] == e.content['items'][item]['description']
        receipt = store.submit(rid, 'equip-%s' % index, index - 1, command)
        store.close()
        store = Repository(path)
        assert store.submit(rid, 'equip-%s' % index, index - 1, command) == receipt
    receipt = store.submit(rid, 'travel', 4, dict(type='travel', destination='switchyard'))
    store.close()
    with Repository(path) as reopened:
        assert reopened.get_view(rid) == receipt['view']
        for actor in receipt['view']['party']:
            item = rewards[actor['template']]
            assert actor['inventory'].count(item) == 1
            assert actor['equipment']['main_hand'] == item


def test_split_and_ground_chinese_menu_confirm_cancel_sqlite_reopen(tmp_path):
    e,s=arena()
    class Fixture(Engine):
        def new_run(self,*args,**kwargs): return deepcopy(s)
    db=tmp_path/'ui.sqlite3'; store=Repository(db,Fixture())
    output=[]; client=Client(LocalTransport(repository=store),output=output.append); client.create()
    client.handle('3')
    entry=next(a for a in client.view['party'][2]['menu_actions'] if a['command'].get('ability')=='magic_missile')
    client.ui.select_action(entry)
    if client.ui.stage == 'slots': client.handle('1')
    client.handle('1,1,2')
    assert client.ui.stage=='confirm'
    assert client.preview_result['command']['targets']==['E1','E1','E2']
    client.handle('0'); assert client.view['party'][2]['slots']['1']==4
    client.handle('1,1,2'); client.handle('c')
    run_id=client.run_id; receipt=client.view; store.close()
    reopened=Repository(db); assert reopened.get_view(run_id)==receipt; reopened.close()
    # Ground UI follows the same preview/confirmation path, including ambiguous E6.
    store=Repository(tmp_path/'ground.sqlite3',Fixture()); client=Client(LocalTransport(repository=store),output=output.append); client.create()
    client.handle('3'); entry=next(a for a in client.view['party'][2]['menu_actions'] if a['command'].get('ability')=='fireball')
    client.ui.select_action(entry); client.handle('e6')
    assert client.preview_result['command']['destination']=='E6'
    client.handle('c'); assert client.view['party'][2]['slots']['3']==1; store.close()


def test_direct_parser_ground_and_split_commands():
    assert parse_line('preview cast P3 fireball at=E6')[1]['destination']=='E6'
    assert parse_line('preview cast P3 fireball G6')[1]['destination']=='G6'
    assert parse_line('preview cast P3 magic_missile E1,E1,E2')[1]['targets']==['E1','E1','E2']


def test_battle_slice_uses_real_map_variants_and_practice_preserves_slice():
    e=PlaygroundEngine([5]*4,battle_slice=True); s=e.new_run(seed=42)
    assert s['battle_slice'] and s['actors']['E1']['resistances']==['fire']
    assert s['actors']['E3']['spell']=={'save':'dex','dc':13}
    safe(s); s,_=e.execute(s,dict(type='practice'))
    assert s['battle_slice'] and s['actors']['E3']['spell']
    assert s['encounter_name']==e.campaign_content['nodes']['forge']['name']
    # Resume through a plain Engine just as the real SQLite/client does.
    assert Engine().view(s)['party'][1]['level']==5


@pytest.mark.parametrize('seed',[1,7,42])
@pytest.mark.parametrize('build',['offense','defense'])
def test_real_battle_slice_two_loadouts_and_three_seeds(seed,build):
    from tools.play_slice import choose, legal
    loadouts = ([dict(type='equip',actor='P1',slot='off_hand',item=None),
                 dict(type='equip',actor='P1',slot='main_hand',item='greatsword_plus1'),
                 dict(type='equip',actor='P2',slot='main_hand',item='striking_staff'),
                 dict(type='equip',actor='P3',slot='main_hand',item='focus_staff')]
                if build=='offense' else
                [dict(type='equip',actor='P1',slot='armor',item='plate'),
                 dict(type='equip',actor='P2',slot='main_hand',item='wind_staff'),
                 dict(type='equip',actor='P3',slot='main_hand',item='warding_staff')])
    loadouts.append(dict(type='equip',actor='P4',slot='main_hand',item='mercy_mace'))
    e=PlaygroundEngine([5]*4,loadouts,battle_slice=True); s=e.new_run(seed=seed)
    for _ in range(500):
        if s['phase']!='combat': break
        operation,payload=choose(e,s)
        # A second policy uses the control loop before normal damage priorities.
        if build=='defense' and operation=='command' and payload.get('actor')=='P2':
            for target in e.view(s)['enemies']:
                c=dict(type='ability',actor='P2',ability='stunning_strike',target=target['id'])
                if 'stunned' not in target['conditions'] and legal(e,s,c):
                    payload=c; break
        s,_=e.react(s,payload) if operation=='reaction' else e.execute(s,payload)
    assert s['phase']=='victory', (seed,build,s['round'])


def test_content_rejects_placeholder_equipment_effects():
    data=deepcopy(DATA); data['items']['wind_staff']['unimplemented_effect']=1
    with pytest.raises(ValueError): validate_growth(data)


def test_enemy_spell_and_fire_resistance_are_actual_rules():
    e,s=arena(); enemy=s['actors']['E2']
    enemy.update(position='F3', range=12, spell=dict(save='dex',dc=13), dice='2d6',damage_type='fire')
    enemy['budgets']['action']=1
    with patch('core.dice.die',side_effect=lambda state,sides:min(10,sides)):
        e._enemy_finish(s,enemy)
    assert any(x['kind']=='ability' and '熔火箭' in x['text'] for x in s['event_log'])
    assert any(x['kind']=='save' and x['details']['dc']==13 for x in s['event_log'])
    target=s['actors']['E1']; target['resistances']=['fire']
    hp=target['hp']; e._damage(s,target,17,'fire','抗性测试')
    assert target['hp']==hp-8


def test_two_stun_sources_expire_independently_even_if_source_dead():
    e,s=arena(); t=s['actors']['E1']
    t['conditions']=['stunned']; t['effects']['stunned']={'P1':2,'P2':3}
    s['round']=2; s['actors']['P1'].update(hp=0,conditions=['dead'])
    e._expire_control(s,s['actors']['P1'])
    assert 'stunned' in t['conditions'] and t['effects']['stunned']=={'P2':3}
    s['round']=3; e._expire_control(s,s['actors']['P2'])
    assert 'stunned' not in t['conditions'] and 'stunned' not in t['effects']


def test_stun_round_trip_receipt_replay_and_cooperative_attack(tmp_path):
    e,s=arena()
    class Fixture(Engine):
        def new_run(self,*args,**kwargs):return deepcopy(s)
    db=tmp_path/'stun.sqlite3'; store=Repository(db,Fixture())
    rid=store.create_run(command_id='new')['run_id']
    c=dict(type='ability',actor='P2',ability='stunning_strike',target='E1')
    with patch('core.dice.die',side_effect=lambda state,sides:min(10,sides)):
        result=store.submit(rid,'stun',0,c)
    assert 'stunned' in next(t for t in result['view']['enemies'] if t['id']=='E1')['conditions']
    store.close(); store=Repository(db)
    assert store.submit(rid,'stun',0,c)==result
    p=store.preview(rid,dict(type='attack',actor='P1',target='E1'))
    assert p['hit_probability'] > 0.95
    with patch('core.dice.die',side_effect=lambda state,sides:min(10,sides)):
        result=store.submit(rid,'followup',1,p['command'])
    assert next(x['details'] for x in result['events'] if x['kind']=='attack')['advantage']
    store.close()


def test_equipment_purchase_and_equip_receipts_persist_without_double_charge(tmp_path):
    e,s=victory()
    s['campaign']['node']='road'  # Arcane weapons arrive in the second batch.
    class Fixture(Engine):
        def new_run(self,*args,**kwargs):return deepcopy(s)
    db=tmp_path/'shop.sqlite3'; store=Repository(db,Fixture()); rid=store.create_run(command_id='new')['run_id']
    buy=dict(type='buy',actor='P3',item='focus_staff')
    result=store.submit(rid,'buy',0,buy)
    assert result['view']['campaign']['gold']==40
    store.close(); store=Repository(db)
    assert store.submit(rid,'buy',0,buy)==result
    cmd=dict(type='equip',actor='P3',slot='main_hand',item='focus_staff')
    result=store.submit(rid,'equip',1,cmd); store.close(); store=Repository(db)
    assert store.submit(rid,'equip',1,cmd)==result
    assert result['view']['party'][2]['equipment']['main_hand']=='focus_staff'
    store.close()


def test_slice_cli_isolated_real_client():
    process=subprocess.run([sys.executable,'-m','tools.dev_play','--battle-slice','--ui','plain'],input='party\nq\n',
                           capture_output=True,text=True,encoding='utf-8',env=os.environ|{'PYTHONIOENCODING':'utf-8'},timeout=30)
    assert process.returncode==0,process.stderr
    assert '熔炉' in process.stdout and '震慑拳' in process.stdout and 'CmdRPG-M3-' in process.stdout
