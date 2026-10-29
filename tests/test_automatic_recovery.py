"""Victory recovery is atomic, free of extra clicks and independent of wall time."""

from copy import deepcopy

import pytest

from client.cli import Client
from client.transport import LocalTransport
from core.engine import Engine, GameError
from saves.storage import Repository


def won(engine,state):
    for a in state['actors'].values():
        if a['side']=='enemy': a.update(hp=0,conditions=['dead'])
    engine._outcome(state)


@pytest.mark.parametrize('templates',[['fighter','monk','wizard','cleric'],['rogue','warlock','warlock','rogue']])
def test_victory_fully_recovers_all_classes_revives_and_pays_twenty_less(templates):
    engine=Engine(); state=engine.new_run(campaign=True,seed=42,templates=templates)
    for a in state['actors'].values():
        if a['side']=='party':
            a.update(hp=0,conditions=['dead'],death_failure=3,temp_hp=8)
            a['slots']={k:0 for k in a['slots']}; a['resources']={k:0 for k in a['resources']}
    report=deepcopy(state['battle_report']); won(engine,state)
    assert state['campaign']['gold']==100  # 40 + 80 - 20, exactly once.
    assert state['automatic_recovery']['deduction']==20
    for a in state['actors'].values():
        if a['side']=='party':
            assert a['hp']==a['max_hp'] and not a['conditions'] and not a['effects']
            assert not a['temp_hp'] and not a['death_failure'] and a['slots']==a['max_slots']
            assert a['resources']['potions']==2
            assert all(a['resources'][k]==v for k,v in a['max_resources'].items())
    assert state['battle_report']==report
    before=deepcopy(state); engine._campaign_reward(state); engine._outcome(state)
    assert state==before


def test_auto_recovery_keeps_bought_extra_potions_and_clears_retired_resource():
    engine=Engine(); state=engine.new_run(seed=42)
    state['actors']['P3']['resources']['potions']=5
    state['actors']['P3']['resources']['arcane_recovery']=1
    state['actors']['P3']['max_resources']['arcane_recovery']=1
    won(engine,state)
    assert state['actors']['P3']['resources']=={'potions':5}
    assert 'arcane_recovery' not in state['actors']['P3']['max_resources']
    assert state['automatic_recovery']['deduction']==0


@pytest.mark.parametrize('command',[dict(type='rest',kind='short'),dict(type='camp'),dict(type='recover',actor='P3',slot=1)])
def test_manual_recovery_is_retired_without_spending_or_rng(command):
    engine=Engine(); state=engine.new_run(campaign=True,seed=42); won(engine,state)
    before=deepcopy(state)
    with pytest.raises(GameError): engine.execute(state,command)
    assert state==before


def test_new_wizard_has_no_arcane_recovery_resource_at_any_level():
    engine=Engine()
    for level in range(1,9):
        state=engine.new_run(seed=42,levels=[level]*4)
        wizard=state['actors']['P3']
        assert 'arcane_recovery' not in wizard['resources'] and 'arcane_recovery' not in wizard['max_resources']


def test_victory_menu_has_no_camp_short_rest_or_arcane_clicks(tmp_path):
    engine=Engine(); state=engine.new_run(campaign=True,seed=42); won(engine,state)
    class Fixed(Engine):
        def new_run(self,*args,**kwargs): return deepcopy(state)
    with Repository(tmp_path/'menu.sqlite3',engine=Fixed()) as store:
        output=[]; client=Client(LocalTransport(repository=store),output=output.append); client.create()
        menu=client.ui.build(); assert menu['1']==('read','recovery') and '15' not in menu
        before=store._load(client.run_id); client.handle('1')
        assert store._load(client.run_id)==before
        client.handle('9')  # P3 management
        assert not any(kind=='manage' and entry[1]['type']=='recover' for kind,entry in client.ui.build().values())


def test_equipped_ki_cap_is_filled_before_next_fight_without_another_fee():
    engine=Engine(); state=engine.new_run(campaign=True,seed=42); won(engine,state)
    # Simulate a legal owned reward raising the cap after victory has recovered.
    state['actors']['P2']['inventory'].append('stillmind_ring')
    state,_=engine.execute(state,dict(type='equip',actor='P2',slot='ring',item='stillmind_ring'))
    assert state['actors']['P2']['resources']['ki']<state['actors']['P2']['max_resources']['ki']
    original=deepcopy(state); engine.preview(state,dict(type='travel',destination='road'))
    assert state==original
    after,_=engine.execute(state,dict(type='travel',destination='road'))
    assert after['campaign']['gold']==100
    assert after['battle_snapshot']['actors']['P2']['resources']['ki']==after['actors']['P2']['max_resources']['ki']


def test_old_victory_is_not_mutated_by_read_but_recovers_before_travel():
    engine=Engine(); state=engine.new_run(campaign=True,seed=42)
    won(engine,state)
    state.pop('automatic_recovery'); state.update(content_version='0.7.0-campaign',rules_version='0.7.0-provisional')
    state['campaign']['gold']=120
    state['actors']['P1'].update(hp=0,conditions=['dead'])
    before=deepcopy(state); engine.view(state); engine.preview(state,dict(type='travel',destination='road'))
    assert state==before
    after,_=engine.execute(state,dict(type='travel',destination='road'))
    assert after['campaign']['gold']==120
    assert after['battle_snapshot']['actors']['P1']['hp']==after['actors']['P1']['max_hp']


def test_old_cleared_reward_does_not_claim_ungranted_new_equipment():
    engine=Engine(); state=engine.new_run(campaign=True,seed=42); won(engine,state)
    state['campaign']['node']='bastion'
    state['campaign']['equipment_awards']={}
    state['actors']['P1']['inventory'].append('protection_ring')
    assert engine.view(state)['settlement']['equipment_rewards']==[]
    assert '铁誓行军靴' not in engine.view(state)['settlement']['loot_description']


def test_last_hit_recovery_and_reward_share_one_sqlite_receipt(tmp_path):
    engine=Engine(); state=engine.new_run(campaign=True,seed=42)
    state['map'].update(walls=[],heights={},hazards={},difficult=[])
    state.update(active_group=['P1'],cursor=1,turn_order=['P1','E1','P2','P3','P4','E2','E3'])
    state['actors']['P1'].update(ended=False,position='B4')
    state['actors']['P1']['budgets']=dict(action=1,bonus=1,reaction=1,movement=9)
    for a in state['actors'].values():
        if a['side']=='enemy': a.update(hp=0,conditions=['dead'])
    state['actors']['E1'].update(hp=1,conditions=[],position='C4',ac=1)
    state['actors']['P3']['slots']={'1':0}
    class Fixed(Engine):
        def new_run(self,*args,**kwargs): return deepcopy(state)
    path=tmp_path/'atomic.sqlite3'
    from unittest.mock import patch
    with Repository(path,engine=Fixed()) as store:
        rid=store.create_run(command_id='new')['run_id']
        with patch('core.dice.d20',return_value=(20,[20])):
            receipt=store.submit(rid,'win',0,dict(type='attack',actor='P1',target='E1'))
        assert receipt['view']['phase']=='victory'
        assert receipt['view']['party'][2]['slots']=={'1':3}
        assert receipt['view']['campaign']['gold']==100
    with Repository(path) as store:
        assert store.submit(rid,'win',0,dict(type='attack',actor='P1',target='E1'))==receipt
        assert store.get_view(rid)==receipt['view']
