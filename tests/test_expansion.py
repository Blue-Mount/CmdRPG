"""Expansion contracts: six real classes, fourteen battles and immutable old routes."""

from copy import deepcopy
import json
import os
import subprocess
import sys
from unittest.mock import patch

import pytest

from core.engine import Engine, GameError
from core.growth import DATA, snapshot
from core.campaign import load_campaign
from saves.storage import Repository
from tools.dev_play import PlaygroundEngine
from tools.play_campaign import play
from tools.play_slice import choose


@pytest.fixture
def arena():
    engine = Engine()
    state = engine.new_run(templates=['rogue', 'warlock', 'fighter', 'cleric'], seed=42, levels=[8]*4)
    state['map'].update(walls=[], heights={}, hazards={}, difficult=[])
    positions = dict(P1='B4', P2='B6', P3='D5', P4='B8', E1='D4', E2='K4', E3='L8')
    for aid, actor in state['actors'].items():
        actor.update(position=positions[aid], ended=False, conditions=[], effects={}, temp_hp=0)
        actor['budgets'] = dict(action=1, bonus=2 if aid=='P1' else 1, reaction=1, movement=actor['speed'])
        if actor['side']=='enemy':
            actor.update(hp=1000, max_hp=1000, ac=15)
    state.update(active_group=['P1','P2','P3','P4'], cursor=4, turn_order=['P1','P2','P3','P4','E1','E2','E3'])
    return engine, state


@pytest.mark.parametrize('cid', ['rogue','warlock'])
@pytest.mark.parametrize('level', range(1,9))
def test_new_classes_have_legal_snapshots_and_visible_working_actions(cid, level):
    engine=Engine()
    state=engine.new_run(templates=[cid]*4, seed=42, levels=[level]*4)
    member=state['actors']['P1']
    assert member['hp']==10+7*(level-1)
    assert member['level']==level
    assert member['abilities']==DATA['classes'][cid][str(level)]['abilities']
    if cid=='warlock':
        assert member['slots']=={str((level+1)//2):1 if level==1 else 2}
        assert all(a in member['prepared'] for a in member['known_spells'])
    engine.view(state)


def test_blast_allocations_match_damage_and_do_not_spend_rng_in_preview(arena):
    engine,state=arena
    command=dict(type='cast',actor='P2',ability='eldritch_blast',targets=['E1','E2'])
    before=deepcopy(state)
    preview=engine.preview(state,command)
    assert state==before and preview['attack_count']==2
    assert preview['cost']=={'action':1}
    assert all(a['damage_bonus']==5 for a in preview['allocations'])
    with patch('core.dice.d20',return_value=(20,[20])):
        after,events=engine.execute(state,command)
    assert after['actors']['P2']['slots']==state['actors']['P2']['slots']
    assert after['actors']['E1']['hp']<1000 and after['actors']['E2']['hp']<1000
    assert len([e for e in events if e['kind']=='attack'])==2
    assert all(e['details']['ability_modifier']==5 for e in events if e['kind']=='damage')


@pytest.mark.parametrize('bad', [dict(targets=['E1']),dict(targets=['E1','unknown']),dict(target='E1',targets=['E1','E2']),dict(target='E1',slot=1)])
def test_invalid_blast_cannot_spend_anything(arena,bad):
    engine,state=arena
    before=deepcopy(state)
    with pytest.raises(GameError):
        engine.execute(state,dict(type='cast',actor='P2',ability='eldritch_blast',**bad))
    assert state==before


def test_blast_respects_armor_proficiency_and_hidden_target(arena):
    engine,state=arena
    actor=state['actors']['P2']
    actor['equipment']['off_hand']='shield'
    with pytest.raises(GameError,match='未熟练'):
        engine.preview(state,dict(type='cast',actor='P2',ability='eldritch_blast',target='E1'))
    actor['equipment']['off_hand']=None
    state['actors']['E2'].update(hidden=True,position='N12')
    with pytest.raises(GameError):
        engine.preview(state,dict(type='cast',actor='P2',ability='eldritch_blast',target='E2'))


def test_pact_upgrade_preserves_spent_slots_across_slot_rank_change():
    engine=Engine()
    state=engine.new_run(templates=['warlock']*4,seed=42,levels=[2]*4)
    actor=state['actors']['P1']
    actor['slots']['1']=0
    snapshot(actor,3,engine.content['items'])
    assert actor['max_slots']=={'2':2} and actor['slots']=={'2':0}
    snapshot(actor,5,engine.content['items'])
    assert actor['slots']=={'3':0}


def test_short_rest_restores_pact_slots_only_and_preview_is_exact(arena):
    engine,state=arena
    state.update(phase='victory',outcome='victory',active_group=[])
    state['actors']['P2']['slots']={'4':0}
    state['actors']['P4']['slots']={k:0 for k in state['actors']['P4']['slots']}
    before=deepcopy(state)
    preview=engine.preview(state,dict(type='rest',kind='short'))
    assert state==before
    assert next(a for a in preview['rest_effects'] if a['actor']=='P2')['slots_restore']=={'4':2}
    after,_=engine.execute(state,dict(type='rest',kind='short'))
    assert after['actors']['P2']['slots']=={'4':2}
    assert not any(after['actors']['P4']['slots'].values())
    assert after['short_rests']==state['short_rests']-1


def test_fiend_temp_hp_is_attributed_nonstacking_and_not_repeated(arena):
    engine,state=arena
    target=state['actors']['E1']; actor=state['actors']['P2']
    engine._damage(state,target,1000,'force','test',source_actor='P2')
    assert actor['temp_hp']==13
    engine._damage(state,target,1000,'force','test',source_actor='P2')
    assert actor['temp_hp']==13
    actor['temp_hp']=20
    engine._damage(state,state['actors']['E2'],1000,'force','test',source_actor='P2')
    assert actor['temp_hp']==20
    engine._damage(state,state['actors']['E3'],1000,'force','test',source_actor='P1')
    assert state['actors']['P1']['temp_hp']==0


def test_false_life_uses_current_pact_rank_and_maximum_not_sum(arena):
    engine,state=arena
    state['actors']['P2']['temp_hp']=40
    after,_=engine.execute(state,dict(type='cast',actor='P2',ability='false_life'))
    assert after['actors']['P2']['temp_hp']==40
    assert after['actors']['P2']['slots']=={'4':1}


def test_sneak_requires_ally_or_net_advantage_and_is_in_preview(arena):
    engine,state=arena
    command=dict(type='attack',actor='P1',target='E1')
    assert engine.preview(state,command)['sneak_dice']=='4d6'
    state['actors']['P3']['position']='B10'
    assert engine.preview(state,command)['sneak_dice'] is None
    state['actors']['E1']['conditions']=['stunned']
    assert engine.preview(state,command)['sneak_dice']=='4d6'
    state['actors']['P1']['conditions']=['prone']
    assert engine.preview(state,command)['sneak_dice'] is None
    state['actors']['P3']['position']='D5'
    assert engine.preview(state,command)['sneak_dice']=='4d6'  # Cancelled rolls, eligible ally.


def test_sneak_dice_critical_resistance_and_once_per_turn(arena):
    engine,state=arena
    target=state['actors']['E1']; target['resistances']=['piercing']
    with patch('core.dice.d20',return_value=(20,[20])), patch('core.dice.roll',side_effect=[(1,{}),(1,{})]) as roll:
        engine._weapon_attack(state,state['actors']['P1'],target)
    # One hit: (bow 1 + Dexterity 5 + sneak 1) // 2 = 3, not independently rounded.
    assert target['hp']==997
    assert roll.call_args_list[1].args[1:]==('4d6',True)
    assert state['actors']['P1']['sneak_used']
    assert engine.preview(state,dict(type='attack',actor='P1',target='E1'))['sneak_dice'] is None
    engine._start_actor(state,state['actors']['P1'])
    assert not state['actors']['P1']['sneak_used'] and state['actors']['P1']['budgets']['bonus']==2


def test_sneak_miss_does_not_consume_use_and_no_ranged_opportunity(arena):
    engine,state=arena
    actor=state['actors']['P1']
    with patch('core.dice.d20',return_value=(1,[1])):
        engine._weapon_attack(state,actor,state['actors']['E1'])
    assert not actor.get('sneak_used')
    actor['position']='D5'
    assert not any(a['id']=='P1' for a in engine._move_reactors(state,state['actors']['E1'],['D4','E4','F4']))


def test_cunning_actions_and_fast_hands_are_real_budgets(arena):
    engine,state=arena
    before=state['actors']['P1']['budgets']['movement']
    state,_=engine.execute(state,dict(type='ability',actor='P1',ability='cunning_dash'))
    assert state['actors']['P1']['budgets']==dict(action=1,bonus=1,reaction=1,movement=before+9)
    state,_=engine.execute(state,dict(type='ability',actor='P1',ability='cunning_disengage'))
    assert state['actors']['P1']['budgets']['bonus']==0
    assert 'disengaged' in state['actors']['P1']['conditions']
    with pytest.raises(GameError):
        engine.execute(state,dict(type='ability',actor='P1',ability='cunning_dash'))


def test_shatter_real_friendly_fire_and_no_preview_hidden_leak(arena):
    engine,state=arena
    state['actors']['P3']['position']='D5'
    state['actors']['E2'].update(position='F4',hidden=True)
    # Keep observers beyond hidden detection range, while blast still covers E2.
    state['actors']['P3']['position']='C6'
    state['actors']['P1']['position']='B3'
    command=dict(type='cast',actor='P2',ability='shatter',destination='E4')
    before=deepcopy(state)
    preview=engine.preview(state,command)
    assert state==before and preview['radius']==3 and preview['damage_dice']=='5d8'
    assert 'E2' not in preview['targets']
    state['actors']['P3']['position']='D5'
    with patch('core.dice.d20',return_value=(1,[1])):
        after,events=engine.execute(state,command)
    assert after['actors']['P3']['hp']<state['actors']['P3']['hp']
    assert after['actors']['E2']['hp']<1000
    assert not any('E2' in e['text'] for e in events)


def test_enemy_healing_is_finite_and_consumes_action(arena):
    engine,state=arena
    enemy=state['actors']['E1']; enemy['support']=dict(dice='2d6+3',uses=1,range=9)
    target=state['actors']['E2']; target.update(hp=10,position='F4')
    assert engine._support_turn(state,enemy)
    assert target['hp']>10 and enemy['support']['uses']==0 and enemy['budgets']['action']==0
    enemy['budgets']['action']=1
    assert not engine._support_turn(state,enemy)


def test_enemy_area_spells_affect_both_sides_without_foreknowledge(arena):
    engine,state=arena
    enemy=state['actors']['E1']
    enemy.update(spell=dict(name='测试爆裂',save='con',dc=14,radius=3),dice='2d6',damage_type='thunder',range=12)
    with patch('core.dice.d20',return_value=(1,[1])):
        engine._enemy_spell(state,enemy,state['actors']['P3'],[state['actors']['P3']])
    assert state['actors']['P3']['hp']<state['actors']['P3']['max_hp']
    assert enemy['hp']<1000  # Its own burst can hurt it.


def test_enemy_multiattack_uses_one_action_and_two_hit_rolls(arena):
    engine,state=arena
    enemy=state['actors']['E1']; enemy.update(attacks=2,position='C4')
    with patch('core.dice.d20',return_value=(1,[1])) as roll:
        engine._enemy_finish(state,enemy)
    assert roll.call_count==2 and enemy['budgets']['action']==0 and enemy['ended']


def test_area_resolution_is_independent_of_sqlite_json_object_order(arena):
    engine,state=arena
    state['actors']['P3']['position']='D5'
    command=dict(type='cast',actor='P2',ability='shatter',destination='E4')
    roundtrip=json.loads(json.dumps(state,sort_keys=True))
    left,left_events=engine.execute(state,command)
    right,right_events=engine.execute(roundtrip,command)
    assert left==right and left_events==right_events
    state['actors']['E1'].update(spell=dict(name='test',save='con',dc=14,radius=3),dice='2d6',damage_type='thunder',range=12)
    roundtrip=json.loads(json.dumps(state,sort_keys=True))
    for work in (state,roundtrip):
        engine._enemy_finish(work,work['actors']['E1'])
    assert state==roundtrip


def test_campaign_has_four_paths_fourteen_fights_and_different_maps():
    data=load_campaign(); nodes=data['nodes']
    paths=[]
    def visit(key,prior):
        route=prior+[key]
        if not nodes[key]['next']: paths.append(route)
        for child in nodes[key]['next']: visit(child,route)
    visit(data['start'],[])
    assert len(paths)==4 and all(len(path)==14 for path in paths)
    for path in paths:
        assert [nodes[k]['reward_level'] for k in path]==[2,2,3,3,4,4,5,5,6,6,7,8,8,8]
    signatures={json.dumps(n['map'],sort_keys=True) for n in nodes.values()}
    assert len(signatures)==16
    assert set(nodes['gate']['equipment_reward'])==set(DATA['classes'])


def test_validated_campaign_cache_never_shares_mutable_content():
    first=load_campaign(); first['nodes']['outpost']['map']['walls'].append('B5')
    first['shop']['greatsword']=1
    second=load_campaign()
    assert 'B5' not in second['nodes']['outpost']['map']['walls']
    assert second['shop']['greatsword']==35
    one,two=Engine(),Engine()
    one.campaign_content['nodes']['outpost']['gold']=999
    assert two.campaign_content['nodes']['outpost']['gold']==80


def test_content_rejects_high_ground_with_no_unoccupied_melee_approach(tmp_path):
    data=load_campaign()
    board=data['nodes']['quarry']['map']
    del board['heights']['J4']; del board['heights']['K4']
    path=tmp_path/'bad-platform.json'
    path.write_text(json.dumps(data,ensure_ascii=False),'utf-8')
    with pytest.raises(ValueError,match='safe melee approach'):
        load_campaign(path)


def test_new_equipment_directions_have_actual_costs_and_combat_effects(arena):
    engine,state=arena
    state.update(phase='victory',outcome='victory',active_group=[])
    command=dict(type='equip',actor='P1',slot='main_hand',item='duelist_rapier')
    preview=engine.preview(state,command)
    assert preview['equipment_comparison']['after']['weapon_range']==1.5
    after,_=engine.execute(state,command)
    assert after['actors']['P1']['attack_stat']=='dex'
    assert after['actors']['P1']['ac']==state['actors']['P1']['ac']+1
    after,_=engine.execute(state,dict(type='equip',actor='P2',slot='main_hand',item='fiend_staff'))
    assert after['actors']['P2']['ac']==state['actors']['P2']['ac']+2
    before_attack=engine.view(after)['party'][1]['combat_stats']['spell_attack']
    after,_=engine.execute(after,dict(type='equip',actor='P2',slot='main_hand',item='focus_staff'))
    assert engine.view(after)['party'][1]['combat_stats']['spell_attack']==before_attack+1
    assert after['actors']['P2']['ac']==state['actors']['P2']['ac']


def test_shortbow_does_not_accidentally_unlock_monk_martial_arts(arena):
    engine,state=arena
    from core.growth import recalculate
    actor=state['actors']['P3']; actor['template']='monk'
    snapshot(actor,8,engine.content['items'])
    actor['equipment']['main_hand']='shortbow'; actor['equipment']['armor']=None; actor['equipment']['off_hand']=None
    recalculate(actor,engine.content['items']); actor['attacked']=True
    with pytest.raises(GameError,match='短弓'):
        engine.preview(state,dict(type='ability',actor='P3',ability='unarmed_strike',target='E1'))


@pytest.mark.parametrize('edition,total',[(1,5),(2,7)])
def test_old_campaigns_can_complete_original_routes_through_legal_commands(edition,total):
    engine=Engine(); state=engine.new_run(seed=42,campaign=True)
    state['campaign']['edition']=edition
    state.update(content_version='0.6.0-campaign',rules_version='0.6.0-provisional')
    completed=[]
    for _ in range(1200):
        if state['phase']=='combat':
            op,command=choose(engine,state)
            state,_=engine.react(state,command) if op=='reaction' else engine.execute(state,command)
            continue
        assert state['phase']=='victory'
        completed.append(state['campaign']['node'])
        if state['campaign']['completed']: break
        state,_=engine.execute(state,dict(type='camp'))
        state,_=engine.execute(state,dict(type='travel',destination=engine.view(state)['campaign']['routes'][0]['id']))
    assert state['campaign']['completed'] and len(completed)==total
    assert state['campaign']['edition']==edition


@pytest.mark.parametrize('size',[(110,36),(132,44),(160,50)])
@pytest.mark.parametrize('actor',['P1','P2'])
def test_new_class_cards_keep_every_action_and_read_only_paging(tmp_path,arena,size,actor):
    import io
    from client.cli import Client
    from client.transport import LocalTransport
    from client.cards import DecisionScreen, columns
    _,state=arena
    class Fixed(Engine):
        def new_run(self,*args,**kwargs): return deepcopy(state)
    with Repository(tmp_path/'six-cards.sqlite3',engine=Fixed()) as store:
        screen=DecisionScreen(io.StringIO())
        client=Client(LocalTransport(repository=store),output=screen.message,presenter=screen.present)
        screen.client=client; client.create(); client.handle(actor[1:])
        before=store._load(client.run_id); frames=[]
        for _ in range(16):
            frame=screen.frame(size); frames.append(frame)
            assert all(columns(line)==size[0]-1 for line in frame.splitlines())
            screen.navigate('n')
        assert all(a['name'] in '\n'.join(frames) for a in client.ui.actor()['menu_actions'])
        assert store._load(client.run_id)==before


@pytest.mark.parametrize('edition,total',[(1,5),(2,7)])
def test_old_editions_keep_route_and_read_only_version_adapter(edition,total):
    engine=Engine(); state=engine.new_run(seed=42,campaign=True)
    state['campaign']['edition']=edition
    state.update(content_version='0.6.0-campaign',rules_version='0.6.0-provisional')
    before=deepcopy(state)
    assert engine.view(state)['campaign']['total']==total
    assert engine._campaign_nodes(state)['road']['next']==['gate']
    assert state==before


@pytest.mark.parametrize('route',['road','ridge'])
@pytest.mark.parametrize('branch',['switchyard','quarry'])
def test_expanded_party_completes_fourteen_real_battles(route,branch):
    state,report=play(['fighter','rogue','warlock','cleric'],seed=42,route=route,branch=branch)
    assert report['completed'], report
    assert len(report['battles'])==14
    assert branch in state['campaign']['cleared']
    assert any(b['activity'].get('abilities',{}).get('eldritch_blast',0)>0 for b in report['battles'])


def test_new_budgets_and_pact_spells_survive_sqlite_receipt_replay(tmp_path, arena):
    engine,state=arena
    class Arena(Engine):
        def new_run(self,*args,**kwargs): return deepcopy(state)
    path=tmp_path/'six.sqlite3'
    store=Repository(path,engine=Arena())
    receipt=store.create_run(command_id='create'); rid=receipt['run_id']
    command=dict(type='cast',actor='P2',ability='eldritch_blast',targets=['E1','E2'])
    receipt=store.submit(rid,'blast',0,command)
    assert store.submit(rid,'blast',0,command)==receipt
    saved=store._load(rid)
    store.close(); store=Repository(path)
    assert store._load(rid)==saved and store.get_view(rid)==receipt['view']
    assert store.submit(rid,'blast',0,command)==receipt
    store.close()


def test_new_cli_and_slice_replay_support_all_six_classes():
    run=subprocess.run([sys.executable,'-m','tools.dev_play','--encounter','archive','--classes','rogue,warlock,wizard,cleric','--ui','plain'],
        input='party\ngrowth P1\nq\n',capture_output=True,text=True,encoding='utf-8',timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    assert run.returncode==0,run.stderr
    assert '游荡者' in run.stdout and '邪术师' in run.stdout and '契约法术位' in run.stdout and '偷袭' in run.stdout
    engine=PlaygroundEngine([8]*4,encounter='archive')
    state=engine.new_run(templates=['rogue','warlock','wizard','cleric'],seed=42)
    for _ in range(600):
        if state['phase']!='combat': break
        op,command=choose(engine,state)
        state,_=engine.react(state,command) if op=='reaction' else engine.execute(state,command)
    assert state['phase']=='victory'
    after,_=engine.execute(state,dict(type='practice'))
    assert after['encounter_slice']=='archive' and after['actors']['P1']['template']=='rogue'
