"""Uniform rewards, genuinely exclusive drops, staged stock and six shop views."""

from copy import deepcopy
import io
import json

import pytest

from client.cards import DecisionScreen, columns
from client.cli import Client
from client.transport import LocalTransport
from core.campaign import load_campaign, _equipment_signature
from core.engine import Engine, GameError
from core.growth import DATA, snapshot
from saves.storage import Repository


def settled(stage=1, templates=None):
    engine=Engine(); state=engine.new_run(seed=42,campaign=True,templates=templates)
    key=next(k for k,n in engine.campaign_content['nodes'].items() if n['stage']==stage)
    state['campaign']['node']=key
    before_level=max((n['reward_level'] for n in engine.campaign_content['nodes'].values() if n['stage']==stage-1),default=1)
    state['campaign']['unlocked_level']=before_level
    for actor in state['actors'].values():
        if actor['side']=='enemy': actor.update(hp=0,conditions=['dead'])
        else: snapshot(actor,before_level,engine.content['items'])
    engine._outcome(state)
    return engine,state


def test_all_four_paths_have_tangible_progress_before_finale():
    c=load_campaign(); nodes=c['nodes']; paths=[]
    def visit(key,path):
        path=path+[key]
        if not nodes[key]['next']: paths.append(path)
        for child in nodes[key]['next']: visit(child,path)
    visit(c['start'],[])
    assert len(paths)==4
    for path in paths:
        previous=1
        for key in path[:-1]:
            node=nodes[key]
            assert node['reward_level']>previous or set(node.get('equipment_reward',{}))==set(DATA['classes'])
            previous=node['reward_level']
        assert previous==8
    assert sorted({n['stage'] for n in nodes.values() if n.get('equipment_reward')})==[2,4,5,6,8,10,13]


def test_every_battle_reward_is_unique_from_all_shop_goods_in_id_and_effect():
    engine=Engine(); c=engine.campaign_content
    sold=set(c['shop'])|set(c['legacy_shop'])
    effects={_equipment_signature(engine.content['items'][key]) for key in sold}
    rewards={item for group in ('nodes','previous_nodes','legacy_nodes') for node in c[group].values() for item in node.get('equipment_reward',{}).values()}
    assert len({item for n in c['nodes'].values() for item in n.get('equipment_reward',{}).values()})==28
    assert not rewards & sold
    assert all(_equipment_signature(engine.content['items'][item]) not in effects for item in rewards)


@pytest.mark.parametrize('stage',[2,4,5,6,8,10,13])
@pytest.mark.parametrize('templates',[['fighter','monk','wizard','cleric'],['rogue','warlock','rogue','warlock']])
def test_rewards_are_class_applicable_once_and_never_auto_equipped(stage,templates):
    engine,state=settled(stage,templates)
    node=engine._campaign_nodes(state)[state['campaign']['node']]
    for actor in (a for a in state['actors'].values() if a['side']=='party'):
        item=node['equipment_reward'][actor['template']]
        assert actor['inventory'].count(item)==1
        assert item not in actor['equipment'].values()
        command=dict(type='equip',actor=actor['id'],slot=engine.content['items'][item]['slot'],item=item)
        # The one two-handed fighter reward requires taking off the shield.
        work=state
        if engine.content['items'][item].get('two_handed'):
            work=engine.execute(state,dict(type='equip',actor=actor['id'],slot='off_hand',item=None))[0]
        preview=engine.preview(work,command)
        assert preview['equipment_comparison']['after']['casting_allowed']
        assert preview['equipment_comparison']['after']['weapon_proficient']
    before=deepcopy(state)
    engine._campaign_reward(state)
    assert state==before


def test_eight_stock_batches_six_stores_and_no_repeat_unlocks():
    engine=Engine(); c=engine.campaign_content
    assert len(c['shop'])==49 and sorted(set(c['shop_unlocks'].values()))==[1,2,4,6,8,10,12,13]
    assert len(c['shop_types'])==6
    previous=set()
    for stage in [1,2,4,6,8,10,12,13]:
        _,state=settled(stage)
        view=engine.view(state)['campaign']
        current={i['id'] for i in view['shop']}
        assert previous<current
        assert {i['id'] for i in view['shop'] if i['new']}==current-previous
        assert sum(s['count'] for s in view['shop_types'])==len(current)
        assert sum(s['new_count'] for s in view['shop_types'])==len(current-previous)
        assert not any(i['id'] in n.get('equipment_reward',{}).values() for i in view['shop'] for n in c['nodes'].values())
        previous=current
    assert previous==set(c['shop'])


def test_old_five_and_seven_station_shops_keep_original_timing_and_prices():
    engine,state=settled(1)
    for edition in (1,2):
        state['campaign']['edition']=edition
        before=deepcopy(state); stock=engine.view(state)['campaign']['shop']
        assert {i['id'] for i in stock}=={k for k,v in engine.campaign_content['legacy_shop_unlocks'].items() if v==1}
        assert 'focus_staff' in {i['id'] for i in stock}
        assert state==before
        state['campaign']['node']='gate'
        stock=engine.view(state)['campaign']['shop']
        assert 'precision_ring' in {i['id'] for i in stock}
        assert 'longsword_plus2' not in {i['id'] for i in stock}
        state['campaign']['node']='outpost'


@pytest.mark.parametrize('mutation',['same_id','renamed_clone','category','coverage','unlock','repeat'])
def test_content_rejects_reward_overlap_and_broken_catalog(tmp_path,mutation):
    data=load_campaign()
    if mutation in ('same_id','renamed_clone'):
        item=data['nodes']['road']['equipment_reward']['fighter']
        # Both an exact reward and its renamed effects are blocked by the validator.
        if mutation=='same_id':
            data['shop'][item]=1; data['shop_unlocks'][item]=1; data['shop_categories'][item]='boots'
        else:
            from core.campaign import _validate_economy
            from core.content import load_content
            items=load_content()['items']|deepcopy(DATA['items'])
            items['swift_boots']=deepcopy(items[item]); items['swift_boots']['name']='renamed shop clone'
            with pytest.raises(ValueError,match='exclusive'):
                _validate_economy(data,items,DATA)
            return
    elif mutation=='category': data['shop_categories']['plate']='boots'
    elif mutation=='coverage': del data['nodes']['road']['equipment_reward']['monk']
    elif mutation=='repeat': data['nodes']['forge']['equipment_reward']['monk']='stillmind_ring'
    else: data['shop_unlocks']['full_plate']=15
    path=tmp_path/'bad-economy.json'; path.write_text(json.dumps(data,ensure_ascii=False),'utf-8')
    with pytest.raises(ValueError): load_campaign(path)


def test_drop_equipment_really_changes_damage_dc_and_ki_without_refilling():
    engine,state=settled(13)
    for actor in ['P1','P2','P3','P4']:
        item=engine._campaign_nodes(state)[state['campaign']['node']]['equipment_reward'][state['actors'][actor]['template']]
        if actor=='P2': state['actors'][actor]['resources']['ki']=1
        before=engine.view(state)['party'][int(actor[1:])-1]['combat_stats']
        state,_=engine.execute(state,dict(type='equip',actor=actor,slot='ring',item=item))
        after=engine.view(state)['party'][int(actor[1:])-1]['combat_stats']
        if actor=='P1': assert after['attack_bonus']==before['attack_bonus']+2 and after['damage_bonus']==before['damage_bonus']+2
        elif actor=='P2': assert state['actors'][actor]['resources']['ki']==1 and state['actors'][actor]['max_resources']['ki']==12
        elif actor=='P3': assert after['spell_dc']==before['spell_dc']+3
        else: assert after['healing_bonus']==before['healing_bonus']+3


def test_category_filter_uses_local_numbering_and_cancel_replay_are_atomic(tmp_path):
    engine,state=settled(8)
    class Fixed(Engine):
        def new_run(self,*args,**kwargs): return deepcopy(state)
    path=tmp_path/'shops.sqlite3'; store=Repository(path,engine=Fixed())
    output=[]; client=Client(LocalTransport(repository=store),output=output.append); client.create()
    rid=client.run_id
    try:
        original=store._load(rid)
        client.handle('16'); client.handle('3'); client.handle('t')
        assert client.ui.stage=='shop_types' and len(client.menu)==7
        client.handle('3')  # Arcane shop, not the third entry in all stock.
        assert client.ui.shop_type=='arcane'
        items=client.ui.shop_items()
        number=str(next(i for i,item in enumerate(items,1) if item['id']=='judgement_staff'))
        client.handle(number); assert client.preview_result['command']['item']=='judgement_staff'
        client.handle('0'); assert client.ui.stage=='shop'
        assert store._load(rid)==original
        client.handle(number); preview=deepcopy(client.preview_result)
        receipt=store.submit(rid,'purchase',original['revision'],preview['command'])
        assert store.submit(rid,'purchase',original['revision'],preview['command'])==receipt
        assert receipt['view']['campaign']['gold']==original['campaign']['gold']-120
        store.close(); store=Repository(path)
        assert store.get_view(rid)==receipt['view']
        assert store.submit(rid,'purchase',original['revision'],preview['command'])==receipt
    finally: store.close()


@pytest.mark.parametrize('size',[(110,36),(132,44),(160,50)])
def test_all_shop_goods_and_stores_are_reachable_in_cards(tmp_path,size):
    _,state=settled(13)
    class Fixed(Engine):
        def new_run(self,*args,**kwargs): return deepcopy(state)
    with Repository(tmp_path/'shop-cards.sqlite3',engine=Fixed()) as store:
        screen=DecisionScreen(io.StringIO()); client=Client(LocalTransport(repository=store),output=screen.message,presenter=screen.present)
        screen.client=client; client.create(); client.handle('16'); client.handle('1')
        before=store._load(client.run_id); frames=[]
        for _ in range(64):
            frame=screen.frame(size); frames.append(frame)
            assert all(columns(line)==size[0]-1 for line in frame.splitlines())
            screen.navigate('n')
        assert all(item['name'] in '\n'.join(frames) for item in client.view['campaign']['shop'])
        client.handle('t')
        assert all(store_type['name'] in client.ui.render() for store_type in client.view['campaign']['shop_types'])
        assert store._load(client.run_id)==before


def test_old_070_snapshot_is_read_only_until_a_successful_write():
    engine,state=settled(2)
    state.update(content_version='0.7.0-campaign',rules_version='0.7.0-provisional')
    before=deepcopy(state); engine.view(state); engine.preview(state,dict(type='buy',actor='P1',item='greatsword'))
    assert state==before
    after,_=engine.execute(state,dict(type='buy',actor='P1',item='greatsword'))
    assert after['content_version']=='0.8.0-campaign'


def test_locked_goods_are_rejected_without_any_state_change():
    engine,state=settled(2); before=deepcopy(state)
    with pytest.raises(GameError): engine.execute(state,dict(type='buy',actor='P1',item='full_plate'))
    assert state==before
