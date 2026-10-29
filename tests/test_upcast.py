"""Player-selected slots: legal UI choices, actual scaling and atomic receipts."""

from copy import deepcopy
import io
from unittest.mock import patch

import pytest

from client.cli import Client
from client.cards import DecisionScreen, columns
from client.transport import LocalTransport
from core.engine import Engine, GameError
from saves.storage import Repository


@pytest.fixture
def arena():
    engine=Engine(); state=engine.new_run(seed=42,levels=[8]*4)
    state['map'].update(walls=[],heights={},hazards={},difficult=[])
    for aid,pos in dict(P1='B4',P2='B5',P3='B6',P4='B7',E1='F4',E2='G5',E3='M10').items():
        a=state['actors'][aid]; a.update(position=pos,ended=False,conditions=[],effects={})
        a['budgets']=dict(action=1,bonus=1,reaction=1,movement=a['speed'])
        if a['side']=='enemy': a.update(hp=1000,max_hp=1000,ac=10)
    state.update(active_group=['P1','P2','P3','P4'],cursor=4,turn_order=['P1','P2','P3','P4','E1','E2','E3'])
    return engine,state


def client_for(tmp_path,arena,screen=None):
    _,state=arena
    class Fixed(Engine):
        def new_run(self,*args,**kwargs): return deepcopy(state)
    store=Repository(tmp_path/'upcast.sqlite3',engine=Fixed())
    client=Client(LocalTransport(repository=store),output=screen.message if screen else lambda _:None,
                  presenter=screen.present if screen else None)
    if screen: screen.client=client
    client.create(); return store,client


def spell(client,aid):
    client.handle('3')
    entry=next(a for a in client.view['party'][2]['menu_actions'] if a['command'].get('ability')==aid)
    client.ui.select_action(entry)


def test_slot_menu_shows_costs_effects_exhaustion_and_no_preview_side_effects(tmp_path,arena):
    arena[1]['actors']['P3']['slots']['2']=0
    store,client=client_for(tmp_path,arena)
    try:
        original=store._load(client.run_id)
        spell(client,'magic_missile')
        assert client.ui.stage=='slots'
        text=client.ui.render()
        assert '6枚飞弹' in text and '已耗尽' in text
        client.handle('2')
        assert client.ui.stage=='slots' and store._load(client.run_id)==original
        client.handle('4')
        assert client.ui.stage=='target' and client.ui.options['max_targets']==6
        client.handle('1')
        assert client.ui.stage=='confirm' and client.preview_result['cost']=={'action':1,'slot_4':1}
        client.handle('0'); client.handle('0')
        assert client.ui.stage=='slots'
        client.handle('1')
        assert client.ui.options['max_targets']==3 and store._load(client.run_id)==original
        client.handle('0'); client.handle('0')
        assert client.ui.stage=='actions' and store._load(client.run_id)==original
    finally: store.close()


def test_upcast_menu_confirm_reopens_and_replays_exact_chosen_rank(tmp_path,arena):
    store,client=client_for(tmp_path,arena)
    try:
        rid=client.run_id; before=deepcopy(client.view['party'][2]['slots'])
        spell(client,'scorching_ray'); client.handle('4'); client.handle('1')
        assert client.preview_result['attack_count']==5
        client.handle('c')
        assert client.view['party'][2]['slots']==before|{'4':before['4']-1}
        assert len([e for e in client.last_result['events'] if e['kind']=='attack'])==5
        saved=store._load(rid); store.close(); store=Repository(tmp_path/'upcast.sqlite3')
        assert store._load(rid)==saved
        assert store.get_view(rid)['party'][2]['slots']['4']==before['4']-1
    finally: store.close()


def test_stale_slot_list_cannot_consume_a_different_remaining_slot(tmp_path,arena):
    store,client=client_for(tmp_path,arena)
    try:
        spell(client,'magic_missile')
        store.submit(client.run_id,'other',0,dict(type='end',actor='P1'))
        client.handle('4')
        assert client.ui.stage=='actions'
        assert store.get_view(client.run_id)['party'][2]['slots']['4']==2
    finally: store.close()


@pytest.mark.parametrize('size',[(110,36),(132,44),(160,50)])
def test_upcast_cards_retain_all_ranks_and_effects_without_writes(tmp_path,arena,size):
    screen=DecisionScreen(io.StringIO()); store,client=client_for(tmp_path,arena,screen)
    try:
        spell(client,'magic_missile'); before=store._load(client.run_id)
        frames=[]
        for _ in range(12):
            frame=screen.frame(size); frames.append(frame)
            assert all(columns(line)==size[0]-1 for line in frame.splitlines())
            screen.navigate('n')
        text='\n'.join(frames)
        assert '6枚飞弹' in text and '4环' in text and '1环' in text
        assert store._load(client.run_id)==before
    finally: store.close()


@pytest.mark.parametrize('aid,slot,count', [('magic_missile',4,6),('scorching_ray',4,5)])
def test_upcast_multisegment_scaling_and_exact_resource_cost(arena,aid,slot,count):
    engine,state=arena
    command=dict(type='cast',actor='P3',ability=aid,slot=slot,target='E1')
    preview=engine.preview(state,command)
    assert preview['attack_count']==count
    with patch('core.dice.d20',return_value=(20,[20])):
        after,events=engine.execute(state,command)
    assert after['actors']['P3']['slots']['4']==1 and after['actors']['P3']['slots']['1']==4
    assert len([e for e in events if e['kind']=='attack_segment'])==count


def test_fourth_rank_fireball_uses_nine_dice_and_matches_preview(arena):
    engine,state=arena
    command=dict(type='cast',actor='P3',ability='fireball',destination='G4',slot=4)
    assert engine.preview(state,command)['damage_dice']=='9d6'
    with patch('core.dice.roll',return_value=(9,{})) as roll:
        after,_=engine.execute(state,command)
    assert roll.call_args_list[0].args[1]=='9d6' and after['actors']['P3']['slots']['4']==1


def test_upcast_healing_bless_and_aid_are_real_not_only_descriptions(arena):
    engine,state=arena
    state['actors']['P1']['hp']=1
    state['actors']['P4']['position']='C4'
    with patch('core.dice.roll',return_value=(4,{})) as roll:
        healed,_=engine.execute(state,dict(type='cast',actor='P4',ability='cure_wounds',target='P1',slot=4))
    assert roll.call_args.args[1]=='4d8'
    assert healed['actors']['P1']['hp']==1+4+5+6
    ids=['P1','P2','P3','P4']
    with pytest.raises(GameError): engine.preview(state,dict(type='cast',actor='P4',ability='bless',targets=ids,slot=1))
    blessed,_=engine.execute(state,dict(type='cast',actor='P4',ability='bless',targets=ids,slot=2))
    assert all('bless' in blessed['actors'][aid]['effects'] for aid in ids)
    aided,_=engine.execute(state,dict(type='cast',actor='P4',ability='aid',slot=4))
    assert aided['actors']['P1']['max_hp']==state['actors']['P1']['max_hp']+15
    assert aided['actors']['P1']['hp']==16


def test_no_scaling_spell_is_explicit_and_still_costs_chosen_slot(arena):
    engine,state=arena
    actor=next(a for a in engine.view(state)['party'] if a['id']=='P3')
    armor=next(a for a in actor['menu_actions'] if a['command'].get('ability')=='mage_armor')
    assert '不随环级增强' in armor['slot_options'][-1]['effect']
    after,_=engine.execute(state,dict(type='cast',actor='P3',ability='mage_armor',target='P3',slot=4))
    assert after['actors']['P3']['ac']==15 and after['actors']['P3']['slots']['4']==1


def test_warlock_has_one_current_pact_rank_and_cantrip_has_no_slot_selector():
    engine=Engine(); state=engine.new_run(templates=['warlock']*4,levels=[8]*4,seed=42)
    actions=engine.menu_actions(state,'P1')
    shatter=next(a for a in actions if a['command'].get('ability')=='shatter')
    assert [o['slot'] for o in shatter['slot_options']]==[4]
    blast=next(a for a in actions if a['command'].get('ability')=='eldritch_blast')
    assert 'slot_options' not in blast
