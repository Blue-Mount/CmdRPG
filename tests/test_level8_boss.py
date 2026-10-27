"""Level-eight builds, equipment slots, phase transitions and recoverable UI."""

from copy import deepcopy
import os
import subprocess
import sys
from unittest.mock import patch

import pytest

from client.cli import Client
from client.parser import parse_line
from client.render import render_growth, render_growth_history, render_preview
from client.cards import DecisionScreen, columns
from client.transport import LocalTransport
from core.engine import Engine, GameError
from core.equipment import stats
from core.growth import DATA, recalculate, validate_growth
from saves.storage import Repository
from tools.dev_play import PlaygroundEngine
from tools.play_slice import choose


def arena(level=8, boss=False):
    engine = PlaygroundEngine([level] * 4, boss_slice=boss)
    state = engine.new_run(seed=42)
    state['map'].update(walls=[], heights={}, hazards={}, difficult=[])
    for actor, cell in zip(state['actors'].values(), ['B2','C2','D2','E2','C3','F3','G3']):
        actor.update(position=cell, ended=False,
                     budgets=dict(action=1, bonus=1, reaction=1, movement=actor['speed']))
    state.update(active_group=['P1','P2','P3','P4'], cursor=4,
                 turn_order=['P1','P2','P3','P4','E1','E2','E3'])
    return engine, state


@pytest.mark.parametrize('cid,aid,hp', [
    ('fighter','P1',[52,60,76]), ('monk','P2',[45,52,59]),
    ('wizard','P3',[38,44,50]), ('cleric','P4',[45,52,59])])
@pytest.mark.parametrize('level', [6,7,8])
def test_high_level_builds_have_real_hp_resources_slots_and_new_features(cid, aid, hp, level):
    engine, state = arena(level)
    actor = state['actors'][aid]
    assert actor['level'] == level and actor['max_hp'] == hp[level - 6]
    assert actor['proficiency'] == 3
    assert set(actor['equipment']) == {'main_hand','off_hand','armor','ring','feet'}
    if cid == 'fighter':
        assert actor['stats']['str'] == 20 and actor['extra_attack'] == 1
        assert actor['max_resources']['superiority'] == (5 if level >= 7 else 4)
    elif cid == 'monk':
        assert actor['speed'] == 15 and actor['resources']['ki'] == level + 1
        assert ('evasion' in actor['passives']) == (level >= 7)
        assert actor['stats']['dex'] == (20 if level == 8 else 18)
    else:
        assert actor['slots'] == ({'1':4,'2':3,'3':3} | ({'4':level - 6} if level >= 7 else {}))
        assert actor['stats']['int' if cid == 'wizard' else 'wis'] == (20 if level == 8 else 18)
        if level >= 7:
            assert ('blight' if cid == 'wizard' else 'death_ward') in actor['prepared']
        if cid == 'cleric':
            assert actor['resources']['channel'] == 2 and 'blessed_healer' in actor['passives']
    assert engine.view(state)['party'][int(aid[1:])-1]['level'] == level


@pytest.mark.parametrize('bad', [0,9,True,7.5])
def test_level_limits_reject_unsupported_or_noninteger_builds(bad):
    with pytest.raises(GameError):
        Engine().new_run(levels=[bad]*4)


def test_accessory_slots_stack_effects_and_keep_monk_and_casting_legal():
    engine, state = arena()
    state.update(phase='victory', outcome='victory', active_group=[])
    before = stats(state['actors']['P2'], engine.content['items'])
    for slot, item in [('ring','protection_ring'), ('feet','swift_boots')]:
        state, _ = engine.execute(state, dict(type='equip',actor='P2',slot=slot,item=item))
    after = stats(state['actors']['P2'], engine.content['items'])
    assert after['ac'] == before['ac'] + 1 and after['speed'] == before['speed'] + 3
    assert after['martial_arts'] and after['casting_allowed']
    state, _ = engine.execute(state, dict(type='equip',actor='P3',slot='feet',item='swift_boots'))
    assert state['actors']['P3']['speed'] == 12
    state, _ = engine.execute(state, dict(type='equip',actor='P3',slot='feet',item=None))
    assert state['actors']['P3']['speed'] == 9
    for _ in range(3):
        recalculate(state['actors']['P2'], engine.content['items'])
    assert stats(state['actors']['P2'], engine.content['items']) == after
    with pytest.raises(GameError):
        engine.execute(state, dict(type='equip',actor='P2',slot='feet',item='protection_ring'))


def test_saving_throw_equipment_preview_and_actual_roll_include_same_bonus():
    engine, state = arena()
    actor = state['actors']['P4']
    actor['equipment'].update(off_hand='warding_shield',feet='resolve_boots')
    recalculate(actor,engine.content['items'])
    # Cleric Dex +0 plus two equipment points: rolls 1..12 fail against DC15.
    assert engine._save_failure_probability(actor,'dex',15) == pytest.approx(12/20)
    with patch('core.dice.die',return_value=13):
        assert engine._save(state,actor,'dex',15,'测试')
    assert state['event_log'][-1]['details']['equipment_bonus'] == 2


@pytest.mark.parametrize('success,expected', [(True,0),(False,6)])
def test_monk_evasion_on_half_damage_dex_saves(success, expected):
    engine, state = arena()
    monk = state['actors']['P2']; hp = monk['hp']
    with patch.object(engine,'_save',return_value=success):
        engine._save_damage(state,monk,12,'fire','dex',15,'范围攻击')
    assert monk['hp'] == hp - expected
    monk['conditions'] = ['stunned']
    with patch.object(engine,'_save',return_value=False):
        engine._save_damage(state,monk,12,'fire','dex',15,'范围攻击')
    assert monk['hp'] == hp - expected - 12


@pytest.mark.parametrize('success,damage', [(False,64),(True,32)])
def test_blight_pure_preview_and_actual_save_damage(success, damage):
    engine, state = arena(); state['actors']['E1'].update(hp=1000,max_hp=1000)
    command = dict(type='cast',actor='P3',ability='blight',target='E1')
    before = deepcopy(state); preview = engine.preview(state,command)
    assert state == before and preview['damage_dice'] == '8d8' and preview['cost']['slot_4'] == 1
    with patch.object(engine,'_save',return_value=success), patch('core.dice.die',side_effect=lambda s,n:n):
        after, _ = engine.execute(state,command)
    assert after['actors']['E1']['hp'] == 1000-damage
    assert after['actors']['P3']['slots']['4'] == 1


def test_death_ward_consumes_once_without_reviving_or_stacking():
    engine, state = arena(); state['actors']['P4']['position']='D3'
    command = dict(type='cast',actor='P4',ability='death_ward',target='P3')
    state, _ = engine.execute(state,command)
    state['actors']['P4']['budgets']['action']=1
    state, _ = engine.execute(state,command)
    wizard = state['actors']['P3']
    engine._damage(state,wizard,100,'fire','致命攻击')
    assert wizard['hp'] == 1 and 'downed' not in wizard['conditions'] and 'death_ward' not in wizard['effects']
    engine._damage(state,wizard,1,'fire','后续攻击')
    assert wizard['hp'] == 0 and 'downed' in wizard['conditions']
    assert sum('死亡防护消耗' in e['text'] for e in state['event_log']) == 1


def test_blessed_healer_triggers_once_for_mass_heal_and_not_potions():
    engine, state = arena()
    for actor in state['actors'].values():
        if actor['side']=='party': actor['hp']=1
    with patch('core.dice.die',side_effect=lambda s,n:n):
        state, events=engine.execute(state,dict(type='cast',actor='P4',ability='mass_healing_word'))
    assert sum('受祝福的医者' in e['text'] for e in events) == 1
    assert state['actors']['P4']['hp'] == 20  # 1 + 1d4(4)+Wis5+Life5 + passive5
    state['actors']['P4']['budgets']['bonus']=1
    _, events=engine.execute(state,dict(type='potion',actor='P4'))
    assert all('受祝福的医者' not in e['text'] for e in events)


def test_divine_strike_once_per_own_turn_critical_and_not_reaction():
    engine, state=arena(); attacker=state['actors']['P4']; target=state['actors']['E1']
    target.update(hp=1000,max_hp=1000)
    with patch('core.dice.die',side_effect=lambda s,n:n):
        for reaction in (True,False,False):
            engine._weapon_attack(state,attacker,target,reaction=reaction)
        divine=[e for e in state['event_log'] if e['kind']=='damage' and '神圣打击' in e['text']]
        assert len(divine)==1 and divine[0]['details']['raw_damage']==16
        engine._start_actor(state,attacker)
        engine._weapon_attack(state,attacker,target)
    assert sum(e['kind']=='damage' and '神圣打击' in e['text'] for e in state['event_log'])==2


def test_boss_transition_once_preserves_stun_and_budgets_and_can_be_burst_killed():
    engine,state=arena(boss=True); boss=state['actors']['E1']
    boss['conditions']=['stunned']; boss['effects']['stunned']={'P2':2}
    boss['budgets']={key:0 for key in boss['budgets']}; budget=deepcopy(boss['budgets'])
    engine._damage(state,boss,90,'force','阶段测试')
    assert boss['boss_phase']==2 and boss['range']==18 and 'fire' in boss['resistances']
    assert boss['conditions']==['stunned'] and boss['budgets']==budget
    engine._damage(state,boss,1,'force','第二次')
    assert sum(e['kind']=='boss_phase' for e in state['event_log'])==1
    engine,state=arena(boss=True); boss=state['actors']['E1']
    engine._damage(state,boss,180,'force','爆发击杀')
    assert boss['hp']==0 and boss.get('boss_phase',1)==1


def test_boss_phase_two_area_and_ranged_fallback_spend_one_action():
    engine,state=arena(boss=True); boss=state['actors']['E1']
    engine._damage(state,boss,90,'force','转阶段')
    with patch('core.dice.die',side_effect=lambda s,n:1):
        engine._enemy_finish(state,boss)
    assert boss['budgets']['action']==0
    assert any('裂隙震荡' in e['text'] for e in state['event_log'])
    # The monk takes half damage even on the failed save.
    assert state['actors']['P2']['hp']==58
    for i in range(1,5): state['actors']['P%s'%i]['position']='N%s'%i
    boss['budgets']['action']=1
    with patch('core.dice.die',side_effect=lambda s,n:1): engine._enemy_finish(state,boss)
    assert any('裂隙火矛' in e['text'] for e in state['event_log'])


def test_phase_and_growth_history_persist_replay_and_retry(tmp_path):
    engine,state=arena(boss=True); state['actors']['E1']['hp']=91
    state['battle_snapshot']=deepcopy(state)
    class Fixture(Engine):
        def new_run(self,*args,**kwargs): return deepcopy(state)
    path=tmp_path/'phase.sqlite3'; store=Repository(path,Fixture())
    rid=store.create_run(command_id='new')['run_id']
    command=dict(type='cast',actor='P3',ability='magic_missile',target='E1')
    receipt=store.submit(rid,'phase',0,command); store.close(); store=Repository(path)
    assert store.submit(rid,'phase',0,command)==receipt
    assert next(e for e in receipt['view']['enemies'] if e['id']=='E1')['boss_phase']==2
    assert len(receipt['view']['party'][2]['growth_history'])==7
    failed=store._load(rid); failed['phase']='defeated'
    restored,_=engine.retry(failed)
    assert restored['actors']['E1'].get('boss_phase',1)==1 and restored['actors']['E1']['hp']==91
    store.close()


def test_levelup_preview_history_and_chinese_read_command_survive_reopen(tmp_path):
    engine,state=arena(6); state.update(phase='victory',outcome='victory',active_group=[])
    class Fixture(Engine):
        def new_run(self,*args,**kwargs): return deepcopy(state)
    path=tmp_path/'growth.sqlite3'; store=Repository(path,Fixture())
    client=Client(LocalTransport(repository=store),output=lambda text:None); client.create()
    command=dict(type='levelup',actor='P3'); before=store._load(client.run_id)
    preview=store.preview(client.run_id,command)
    assert '新能力：枯萎术' in preview['growth_changes']
    assert '4环法术位上限 0 → 1' in render_preview(1,preview,0,client.view)
    assert store._load(client.run_id)==before
    receipt=store.submit(client.run_id,'upgrade',0,command); rid=client.run_id; store.close()
    with Repository(path) as reopened:
        assert reopened.submit(rid,'upgrade',0,command)==receipt
        output=[]; recovered=Client(LocalTransport(repository=reopened),rid,output=output.append)
        recovered.handle('growth P3')
        assert '6 → 7级' in output[-1] and '枯萎术' in output[-1]
        assert '最近成长：6 → 7级' in render_growth(receipt['view']['party'][2])
        recovered.handle('17')
        assert any('升级记录' in text and 'P1' in text and 'P4' in text for text in output)


def test_campaign_cross_level_gains_are_emitted_per_level_and_not_duplicated():
    engine=Engine(); state=engine.new_run(campaign=True,seed=42)
    for actor in state['actors'].values():
        if actor['side']=='enemy': actor.update(hp=0,conditions=['dead'])
    engine._outcome(state)
    state['campaign']['node']='road'; previous=state['event_seq']
    engine._campaign_reward(state)
    events=[e for e in state['event_log'] if e['seq']>previous and e['kind']=='levelup']
    assert len(events)==8
    for aid in ('P1','P2','P3','P4'):
        assert [e['details']['level'] for e in events if e['details']['actor']==aid]==[3,4]
        assert [r['level'] for r in state['actors'][aid]['growth_history']]==[2,3,4]
    before=deepcopy(state); engine._campaign_reward(state); assert state==before


@pytest.mark.parametrize('size',[(110,36),(132,44),(160,50)])
def test_eight_level_cards_retain_every_ability_and_stay_within_terminal(tmp_path,size):
    import io
    store=Repository(tmp_path/'cards.sqlite3',PlaygroundEngine([8]*4,boss_slice=True))
    screen=DecisionScreen(io.StringIO()); client=Client(LocalTransport(repository=store),output=screen.message,presenter=screen.present)
    screen.client=client; client.create(); client.handle('3')
    text=[]
    for _ in range(15):
        frame=screen.frame(size)
        assert all(columns(line)==size[0]-1 for line in frame.splitlines())
        text.append(frame); screen.navigate('n')
    assert all(entry['name'] in '\n'.join(text) for entry in client.ui.actor()['menu_actions'])
    store.close()


def test_old_equipment_view_and_preview_do_not_mutate_save_and_explicit_equip_adds_slots():
    engine,state=arena(5); state.update(phase='victory',outcome='victory',active_group=[])
    state.update(content_version='0.3.0-m3',rules_version='0.3.0-provisional')
    for actor in state['actors'].values():
        if actor['side']=='party':
            actor['equipment'].pop('ring'); actor['equipment'].pop('feet')
            actor.pop('growth_history',None); actor.pop('last_growth',None)
    before=deepcopy(state); view=engine.view(state)
    assert '旧档更新前的历史不会补造' in render_growth_history(view,'P3')
    command=dict(type='equip',actor='P3',slot='ring',item='protection_ring')
    engine.preview(state,command); assert state==before
    after,_=engine.execute(state,command)
    assert after['actors']['P3']['equipment']['ring']=='protection_ring'
    assert after['content_version']=='0.6.0-m6'


@pytest.mark.parametrize('seed',[1,7,42])
def test_real_eight_level_boss_slice_can_finish_with_legal_commands_and_repeat(seed):
    engine=PlaygroundEngine([8]*4,boss_slice=True); state=engine.new_run(seed=seed)
    phases=0
    for _ in range(600):
        if state['phase']!='combat': break
        operation,payload=choose(engine,state)
        state,events=engine.react(state,payload) if operation=='reaction' else engine.execute(state,payload)
        phases+=sum(e['kind']=='boss_phase' for e in events)
    assert state['phase']=='victory' and phases==1
    state,_=Engine().execute(state,dict(type='practice'))
    assert state['boss_slice'] and state['actors']['E1'].get('boss_phase',1)==1
    assert state['actors']['P3']['level']==8


def test_boss_slice_cli_growth_history_fourth_slot_and_content_validation():
    result=subprocess.run([sys.executable,'-m','tools.dev_play','--boss-slice','--ui','plain'],input='growth P3\nparty\nq\n',capture_output=True,text=True,encoding='utf-8',env=os.environ|{'PYTHONIOENCODING':'utf-8'},timeout=30)
    assert result.returncode==0,result.stderr
    assert '裂隙之心' in result.stdout and '枯萎术' in result.stdout and '7 → 8级' in result.stdout
    assert parse_line('preview recover P3 4')[1]['slot']==4
    broken=deepcopy(DATA); broken['items']['swift_boots']['slot']='ring'; broken['items']['swift_boots']['kind']='armor'
    with pytest.raises(ValueError): validate_growth(broken)
