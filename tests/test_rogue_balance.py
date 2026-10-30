"""Rogue bonus actions share preview, equipment, combat and persistence rules."""
from copy import deepcopy
import json
from unittest.mock import patch

import pytest

from core.engine import Engine, GameError
from core.growth import recalculate


@pytest.fixture
def arena():
    e = Engine()
    s = e.new_run(templates=['rogue', 'fighter', 'wizard', 'cleric'], levels=[5]*4, seed=42)
    s['map'].update(walls=[], heights={}, hazards={}, difficult=[])
    cells = dict(P1='B4', P2='D5', P3='B8', P4='B9', E1='D4', E2='M4', E3='M8')
    for aid, a in s['actors'].items():
        a.update(position=cells[aid], ended=False, conditions=[], effects={})
        a['budgets'] = dict(action=1, bonus=2, reaction=1, movement=a['speed'])
        if a['side'] == 'enemy': a.update(hp=1000, max_hp=1000, ac=15)
    s.update(active_group=['P1', 'P2', 'P3', 'P4'], cursor=4,
             turn_order=list(cells), pending=None)
    return e, s


def dual(e, s):
    a = s['actors']['P1']
    a['equipment'].update(main_hand='duelist_rapier', off_hand='offhand_dagger')
    recalculate(a, e.content['items'])
    a['position'] = 'C4'
    return a


def test_offhand_requires_real_slot_melee_and_attack_action(arena):
    e, s = arena
    a = dual(e, s)
    cmd = dict(type='ability', actor='P1', ability='offhand_attack', target='E1')
    before = deepcopy(s)
    with pytest.raises(GameError): e.execute(s, cmd)
    assert s == before
    a['attacked'] = True
    for slot, item in [('off_hand', None), ('main_hand', 'shortbow')]:
        copy = deepcopy(s)
        copy['actors']['P1']['equipment'][slot] = item
        recalculate(copy['actors']['P1'], e.content['items'])
        with pytest.raises(GameError): e.preview(copy, cmd)
    assert e.preview(s, cmd)['cost'] == {'bonus': 1}
    assert any(x['name'] == '副手攻击' and x['available'] for x in e.menu_actions(s, 'P1'))


def test_missed_main_then_offhand_sneaks_only_once_and_restores_equipment(arena):
    e, s = arena
    a = dual(e, s)
    original = deepcopy(a['equipment'])
    with patch('core.dice.d20', return_value=(1, [1])):
        s, _ = e.execute(s, dict(type='attack', actor='P1', target='E1'))
    assert not s['actors']['P1'].get('sneak_used')
    cmd = dict(type='ability', actor='P1', ability='offhand_attack', target='E1')
    before = deepcopy(s)
    p = e.preview(s, cmd)
    assert p['sneak_dice'] == '3d6' and s == before
    with patch('core.dice.d20', return_value=(20, [20])):
        s, events = e.execute(s, cmd)
    a = s['actors']['P1']
    assert a['equipment'] == original and a['weapon'] == 'duelist_rapier'
    assert a['budgets']['bonus'] == 1 and a['budgets']['action'] == 0
    hit = next(x['details'] for x in events if x['kind'] == 'attack')
    damage = next(x['details'] for x in events if x['kind'] == 'damage')
    assert hit['equipment_bonus'] == 0  # Main-hand enchantment cannot leak.
    assert damage['ability_modifier'] == 0
    assert 'sneak_attack' in damage and a['sneak_used']
    assert e.preview(s, cmd)['sneak_dice'] is None
    with patch('core.dice.d20', return_value=(20, [20])):
        s, events = e.execute(s, cmd)
    assert not any('sneak_attack' in x['details'] for x in events if x['kind'] == 'damage')
    with pytest.raises(GameError): e.execute(s, cmd)


def test_main_hit_consumes_sneak_and_cannot_double_on_offhand(arena):
    e, s = arena
    dual(e, s)
    with patch('core.dice.d20', return_value=(20, [20])):
        s, _ = e.execute(s, dict(type='attack', actor='P1', target='E1'))
    assert e.preview(s, dict(type='ability', actor='P1', ability='offhand_attack', target='E1'))['sneak_dice'] is None


def hide_arena(e, s):
    s['actors']['E1']['position'] = 'H4'
    s['actors']['P2']['position'] = 'B6'
    return dict(type='ability', actor='P1', ability='cunning_hide')


@pytest.mark.parametrize('roll,success', [(1, False), (20, True)])
def test_hide_roll_cost_purity_and_enemy_targeting(arena, roll, success):
    e, s = arena
    cmd = hide_arena(e, s)
    before = deepcopy(s)
    p = e.preview(s, cmd)
    assert s == before and p['stealth_dc'] >= 10
    with patch('core.dice.d20', return_value=(roll, [roll])):
        s, events = e.execute(s, cmd)
    assert bool(s['actors']['P1'].get('stealth')) == success
    assert s['actors']['P1']['budgets']['bonus'] == 1
    assert any(x['kind'] == 'stealth' and x['details']['success'] == success for x in events)
    assert ('P1' in [a['id'] for a in e._enemy_targets(s, s['actors']['E1'])]) != success


def test_hidden_attack_has_advantage_sneaks_and_miss_breaks_hiding(arena):
    e, s = arena
    cmd = hide_arena(e, s)
    with patch('core.dice.d20', return_value=(20, [20])): s, _ = e.execute(s, cmd)
    before = deepcopy(s)
    restored = json.loads(json.dumps(s))
    attack = dict(type='attack', actor='P1', target='E1')
    p = e.preview(s, attack)
    assert p['attack_roll']['advantage'] and p['sneak_dice'] == '3d6'
    assert s == before and e.preview(restored, attack) == p
    with patch('core.dice.d20', return_value=(1, [1, 1])):
        s, events = e.execute(s, attack)
        replay, replay_events = e.execute(restored, attack)
    assert s == replay and events == replay_events
    assert not s['actors']['P1'].get('stealth')
    assert not s['actors']['P1'].get('sneak_used')


def test_hide_close_range_rejected_and_turn_expiry(arena):
    e, s = arena
    cmd = dict(type='ability', actor='P1', ability='cunning_hide')
    before = deepcopy(s)
    with pytest.raises(GameError): e.execute(s, cmd)
    assert s == before
    hide_arena(e, s)
    with patch('core.dice.d20', return_value=(20, [20])): s, _ = e.execute(s, cmd)
    with pytest.raises(GameError): e.execute(s, cmd)
    e._start_actor(s, s['actors']['P1'])
    assert not s['actors']['P1'].get('stealth')
    assert s['actors']['P1']['budgets']['bonus'] == 2


@pytest.mark.parametrize('version', ['0.8.0-provisional', '0.9.0-tactical-lab', '0.10.0-rogue'])
def test_old_save_adapter_is_read_only_and_persists_on_command(arena, version):
    e, s = arena
    a = dual(e, s)
    s['rules_version'] = version
    if version == '0.9.0-tactical-lab': s['content_version'] = version
    s.pop('balance_revision')
    a['abilities'] = [x for x in a['abilities'] if x not in ('offhand_attack', 'cunning_hide')]
    a['ac'] += 1
    before = deepcopy(s)
    e.view(s)
    assert s == before
    upgraded, _ = e.execute(s, dict(type='ability', actor='P1', ability='cunning_disengage'))
    assert upgraded['balance_revision'] == 1
    assert upgraded['rules_version'] == '0.10.0-rogue'
    assert upgraded['actors']['P1']['ac'] == a['ac'] - 1
    assert 'offhand_attack' in upgraded['actors']['P1']['abilities']


def test_two_handed_weapon_cannot_equip_offhand_and_new_shop_stock(arena):
    e, s = arena
    s.update(phase='victory', outcome='victory', active_group=[])
    with pytest.raises(GameError):
        e.execute(s, dict(type='equip', actor='P1', slot='off_hand', item='offhand_dagger'))
    assert e.campaign_content['shop']['offhand_dagger'] == 10
    assert e.campaign_content['shop_unlocks']['offhand_dagger'] == 1


def test_damage_and_approaching_enemy_reveal_hidden_rogue(arena):
    e, s = arena
    cmd = hide_arena(e, s)
    with patch('core.dice.d20', return_value=(20, [20])): s, _ = e.execute(s, cmd)
    copy = deepcopy(s)
    e._damage(copy, copy['actors']['P1'], 1, 'fire', 'test')
    assert not copy['actors']['P1'].get('stealth')
    enemy = s['actors']['E1']
    enemy['position'] = 'E4'
    e._continue_move(s, enemy, ['D4'])
    assert not s['actors']['P1'].get('stealth')


@pytest.mark.parametrize('ability', ['offhand_attack', 'cunning_hide'])
def test_real_cli_confirm_cancel_and_sqlite_restart(arena, tmp_path, ability):
    from client.cli import Client
    from client.transport import LocalTransport
    from saves.storage import Repository
    e, s = arena
    if ability == 'offhand_attack': dual(e, s)['attacked'] = True
    else: hide_arena(e, s)
    class Fixed(Engine):
        def new_run(self, *args, **kwargs): return deepcopy(s)
    path = tmp_path / 'rogue.sqlite3'
    store = Repository(path, engine=Fixed())
    client = Client(LocalTransport(repository=store), output=lambda text: None)
    client.create()
    before = store._load(client.run_id)
    def select():
        client.handle('1')
        n = next(i for i, a in enumerate(client.ui.actor()['menu_actions'], 1)
                 if a['command'].get('ability') == ability)
        client.handle(str(n))
        if ability == 'offhand_attack':
            assert client.ui.stage == 'target'
            n = next(i for i, x in enumerate(client.ui.entries, 1) if x['target'] == 'E1')
            client.handle(str(n))
        assert client.ui.stage == 'confirm'
    select()
    client.handle('0')
    assert store._load(client.run_id) == before
    while client.ui.stage != 'home': client.handle('0')
    select()
    with patch('core.dice.d20', return_value=(20, [20])): client.handle('c')
    saved = store._load(client.run_id)
    assert saved['revision'] == before['revision'] + 1
    rid = client.run_id
    view = store.get_view(rid)
    store.close()
    with Repository(path) as reopened:
        assert reopened._load(rid) == saved and reopened.get_view(rid) == view
