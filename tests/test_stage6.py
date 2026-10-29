"""Player-facing combat facts, real equipment effects and versioned campaigns."""

from copy import deepcopy
import os
import subprocess
import sys
from unittest.mock import patch

import pytest

from client.render import render_actor_card, render_party, render_preview, render_result
from core.engine import Engine, GameError
from core.growth import snapshot
from saves.storage import Repository
from tools.dev_play import PlaygroundEngine
from tools.play_slice import choose


@pytest.fixture
def arena():
    engine = Engine()
    state = engine.new_run(seed=42, levels=[8] * 4)
    state['map'].update(walls=[], heights={}, hazards={}, difficult=[])
    for aid, pos in dict(P1='B4', P2='B5', P3='B6', P4='B7', E1='C4', E2='L2', E3='N2').items():
        actor = state['actors'][aid]
        actor.update(position=pos, ended=False, conditions=[], effects={})
        actor['budgets'] = dict(action=1, bonus=1, reaction=1, movement=actor['speed'])
        if actor['side'] == 'enemy':
            actor.update(hp=1000, max_hp=1000, ac=17)
    state.update(active_group=['P1','P2','P3','P4'], cursor=4,
                 turn_order=['P1','P2','P3','P4','E1','E2','E3'])
    return engine, state


def equip(engine, state, actor, slot, item):
    state.update(phase='victory', outcome='victory', active_group=[])
    return engine.execute(state, dict(type='equip', actor=actor, slot=slot, item=item))[0]


def activate(state):
    state.update(phase='combat', outcome=None, active_group=['P1','P2','P3','P4'])


def test_panel_proficiencies_attack_budget_and_spell_numbers_are_live(arena):
    engine, state = arena
    original = deepcopy(state)
    view = engine.view(state)
    text = render_party(view)
    assert state == original
    assert '盾牌熟练：有' in render_actor_card(view['party'][0])
    assert '盾牌熟练：无' in render_actor_card(view['party'][2])
    assert '气点 9/9' in text and '4环 2/2' in text and '豁免DC 16' in text
    command = dict(type='attack', actor='P1', target='E1')
    for remaining in (1, 0):
        state, _ = engine.execute(state, command)
        assert engine.view(state)['party'][0]['combat_stats']['attacks_remaining'] == remaining
    state, _ = engine.execute(state, dict(type='ability',actor='P1',ability='action_surge'))
    assert engine.view(state)['party'][0]['combat_stats']['attacks_remaining'] == 2
    state, _ = engine.execute(state, dict(type='end',actor='P1'))
    assert engine.view(state)['party'][0]['combat_stats']['attacks_remaining'] == 0


def test_precision_ring_changes_real_hit_and_explicit_ac_tradeoff(arena):
    engine, state = arena
    state = equip(engine,state,'P1','ring','protection_ring')
    comparison = engine.preview(state,dict(type='equip',actor='P1',slot='ring',item='precision_ring'))
    text = render_preview(1,comparison,state['revision'])
    assert '（+2）' in text and '（-1）' in text
    base = deepcopy(state)
    changed = equip(engine,state,'P1','ring','precision_ring')
    for sample, expected in ((base,False),(changed,True)):
        activate(sample)
        with patch('core.dice.die', return_value=8):
            _,events=engine.execute(sample,dict(type='attack',actor='P1',target='E1'))
        event=next(e for e in events if e['kind']=='attack')
        assert event['details']['hit'] is expected
        assert event['details']['equipment_bonus'] == (2 if expected else 0)


def test_weave_ring_changes_actual_spell_dc_and_strider_boots_allow_longer_move(arena):
    engine,state=arena
    state=equip(engine,state,'P3','ring','weave_ring')
    activate(state)
    state['actors']['E1']['position']='D6'
    with patch('core.dice.die',side_effect=lambda s,n:16 if n==20 else 1):
        _,events=engine.execute(state,dict(type='cast',actor='P3',ability='blight',target='E1'))
    save=next(e['details'] for e in events if e['kind']=='save')
    assert save['dc']==18 and not save['success']
    assert engine.view(state)['party'][2]['combat_stats']['spell_dc']==18
    state=equip(engine,state,'P4','feet','strider_boots')
    activate(state); engine._start_actor(state,state['actors']['P4'])
    preview=engine.preview(state,dict(type='move',actor='P4',destination='K7'))
    assert preview['cost']['movement']==13.5
    assert state['actors']['P4']['budgets']['movement']==15


def test_ki_ring_cap_recovery_swap_and_growth_preserve_consumption(arena):
    engine,state=arena
    state['actors']['P2']['resources']['ki']=3
    state=equip(engine,state,'P2','ring','ki_ring')
    assert state['actors']['P2']['max_resources']['ki']==11
    assert state['actors']['P2']['resources']['ki']==3
    for _ in range(3):
        state=equip(engine,state,'P2','ring',None)
        state=equip(engine,state,'P2','ring','ki_ring')
    assert state['actors']['P2']['resources']['ki']==3
    state,_=engine.execute(state,dict(type='rest',kind='short'))
    assert state['actors']['P2']['resources']['ki']==11
    state=equip(engine,state,'P2','ring',None)
    state=equip(engine,state,'P2','ring','ki_ring')
    assert state['actors']['P2']['resources']['ki']==9
    snapshot(state['actors']['P2'],8,engine.content['items'])
    assert state['actors']['P2']['resources']['ki']==9
    assert state['actors']['P2']['max_resources']['ki']==11
    other=equip(engine,state,'P3','ring','ki_ring')
    assert 'ki' not in other['actors']['P3']['max_resources']


def test_ki_ring_while_leveling_and_persisting_does_not_duplicate_bonus(tmp_path):
    engine=Engine(); state=engine.new_run(levels=[6]*4,seed=42)
    state=equip(engine,state,'P2','ring','ki_ring')
    state['actors']['P2']['resources']['ki']=3
    old_cap=state['actors']['P2']['max_resources']['ki']
    snapshot(state['actors']['P2'],7,engine.content['items'])
    actor=state['actors']['P2']; assert actor['resources']['ki']==3+actor['max_resources']['ki']-old_cap
    class Fixed(Engine):
        def new_run(self,*args,**kwargs): return deepcopy(state)
    path=tmp_path/'ki.sqlite3'
    with Repository(path,engine=Fixed()) as store:
        receipt=store.create_run(seed=42,command_id='new'); rid=receipt['run_id']
        command=dict(type='equip',actor='P2',slot='ring',item='ki_ring')
        revision=receipt['view']['revision']
        first=store.submit(rid,'equip',revision,command)
        assert store.submit(rid,'equip',revision,command)==first
    with Repository(path) as store:
        saved=store.get_view(rid)['party'][1]
        assert saved['max_resources']['ki']==actor['max_resources']['ki']
        assert saved['resources']['ki']==actor['resources']['ki']


def test_bless_duration_and_break_reason_are_visible_and_persist(tmp_path,arena):
    engine,state=arena
    state,_=engine.execute(state,dict(type='cast',actor='P4',ability='bless',targets=['P1','P2','P4']))
    view=engine.view(state)
    assert 'P4剩余10回合' in render_actor_card(view['party'][0])
    with patch('core.dice.die',return_value=1):
        before=deepcopy(state); engine._damage(state,state['actors']['P4'],1,'fire','测试')
    result=render_result(state['event_log'],engine.view(before),engine.view(state))
    assert '专注变化：' in result and '受伤豁免失败' in result
    assert '最近专注结束：祝福术 · 受伤豁免失败' in render_actor_card(engine.view(state)['party'][3])
    assert 'bless' not in state['actors']['P1']['effects']
    class Fixed(Engine):
        def new_run(self,*args,**kwargs): return deepcopy(state)
    path=tmp_path/'bless.sqlite3'
    with Repository(path,engine=Fixed()) as store:
        rid=store.create_run(seed=42,command_id='new')['run_id']
    with Repository(path) as store:
        assert store.get_view(rid)['party'][3]['last_concentration_end']['reason']=='受伤豁免失败'


def test_old_five_station_campaign_is_read_only_and_travels_directly_to_old_boss():
    engine=Engine(); state=engine.new_run(seed=42,campaign=True)
    state['campaign'].pop('edition')
    state['campaign'].update(node='forge',cleared=['outpost','road','gate','forge'],unlocked_level=8)
    state.update(content_version='0.5.0-campaign',rules_version='0.5.0-provisional',phase='victory',outcome='victory',active_group=[])
    original=deepcopy(state)
    assert engine.view(state)['campaign']['total']==5
    engine.preview(state,dict(type='travel',destination='heart'))
    assert state==original
    next_state,_=engine.execute(state,dict(type='travel',destination='heart'))
    assert next_state['campaign']['node']=='heart' and engine.view(next_state)['campaign']['total']==5
    assert next_state['actors']['E1']['max_hp']==180
    invalid=deepcopy(state); invalid['campaign']['edition']=99
    with pytest.raises(GameError,match='版本'): engine.view(invalid)


@pytest.mark.parametrize('node',['crossfire','crucible'])
@pytest.mark.parametrize('seed',[7,42])
def test_new_full_level_encounters_finish_and_practice_preserves_scene(node,seed):
    engine=PlaygroundEngine([8]*4,encounter=node)
    state=engine.new_run(seed=seed)
    for _ in range(600):
        if state['phase']!='combat': break
        op,payload=choose(engine,state)
        state,_=engine.react(state,payload) if op=='reaction' else engine.execute(state,payload)
    assert state['phase']=='victory'
    replay,_=engine.execute(state,dict(type='practice'))
    assert replay['encounter_slice']==node and replay['battle_snapshot']['encounter_slice']==node
    assert replay['encounter_name']==state['encounter_name']
    assert all(a['level']==8 for a in replay['actors'].values() if a['side']=='party')


@pytest.mark.parametrize('node',['crossfire','crucible'])
def test_new_encounter_cli_uses_isolated_save_and_shows_live_panel(node):
    run=subprocess.run([sys.executable,'-m','tools.dev_play','--encounter',node,'--ui','plain'],
        input='party\nq\n',capture_output=True,text=True,encoding='utf-8',timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    assert run.returncode==0,run.stderr
    assert '8/8/8/8' in run.stdout and '盾牌熟练：' in run.stdout and '豁免DC' in run.stdout


@pytest.mark.parametrize('route',['road','ridge'])
def test_fourteen_station_campaign_with_all_four_purchased_items(route):
    engine=Engine(); state=engine.new_run(seed=7,campaign=True)
    battles=[]; purchases=[]
    for _ in range(1200):
        if state['phase']=='combat':
            operation,payload=choose(engine,state)
            state,_=engine.react(state,payload) if operation=='reaction' else engine.execute(state,payload)
            continue
        assert state['phase']=='victory'
        progress=state['campaign']; battles.append(progress['node'])
        if progress['completed']: break
        goods={'gate':[('P1','precision_ring','ring'),('P2','ki_ring','ring')],
               'forge':[('P3','weave_ring','ring'),('P4','strider_boots','feet')]}.get(progress['node'],[])
        for actor,item,slot in goods:
            for command in (dict(type='buy',actor=actor,item=item),dict(type='equip',actor=actor,slot=slot,item=item)):
                engine.preview(state,command);state,_=engine.execute(state,command)
            purchases.append(item)
        state,_=engine.execute(state,dict(type='camp'))
        routes=engine.view(state)['campaign']['routes']
        destination=route if any(r['id']==route for r in routes) else routes[0]['id']
        state,_=engine.execute(state,dict(type='travel',destination=destination))
    assert state['campaign']['completed'] and len(battles)==14
    assert len(purchases)==4 and state['actors']['P2']['max_resources']['ki']==11
    assert engine.view(state)['party'][3]['combat_stats']['speed']==15


def test_fresh_loadout_and_practice_fill_equipment_ki_cap():
    engine=PlaygroundEngine([8]*4,loadouts=[dict(type='equip',actor='P2',slot='ring',item='ki_ring')],encounter='crucible')
    state=engine.new_run(seed=42)
    assert state['actors']['P2']['max_resources']['ki']==11
    assert state['actors']['P2']['resources']['ki']==11
    state.update(phase='victory',outcome='victory',active_group=[])
    replay,_=engine.execute(state,dict(type='practice'))
    assert replay['actors']['P2']['resources']['ki']==11
