"""Tactical decisions use the same atomic command/UI/save path as normal play."""

from copy import deepcopy
import io
import json
from unittest.mock import patch

import pytest

from client.cli import Client
from client.cards import DecisionScreen, columns
from client.render import render_map
from client.transport import LocalTransport
from core.engine import Engine, GameError
from core.growth import DATA, validate_growth
from core.playground import GEAR
from saves.storage import Repository
from tools.dev_play import PlaygroundEngine


PARTY = ['fighter', 'rogue', 'wizard', 'cleric']


def arena(scene='foundry', gear=True):
    setup = [dict(type='equip', actor=f'P{i}', slot=GEAR[c][0], item=GEAR[c][1])
             for i, c in enumerate(PARTY, 1) if gear and c in GEAR]
    setup.append(dict(type='equip', actor='P2', slot='main_hand', item='rapier'))
    e = PlaygroundEngine([5] * 4, setup, encounter=scene, tactical=True)
    s = e.new_run(templates=PARTY, seed=42)
    s['map'].update(walls=[], heights={}, hazards={}, difficult=[])
    cells = ['D3', 'C3', 'B2', 'B3', 'D4', 'M10', 'M11', 'N10', 'N11']
    for a, cell in zip(s['actors'].values(), cells):
        a.update(position=cell, ended=False)
        a['budgets'] = dict(action=1, bonus=1, reaction=1, movement=a['speed'])
        if a['side'] == 'enemy':
            a.update(hp=1000, max_hp=1000, ac=10)
    s.update(active_group=['P1', 'P2', 'P3', 'P4'], cursor=4,
             turn_order=list(s['actors']), pending=None)
    return e, s


@pytest.mark.parametrize('scene', ['foundry', 'archive', 'sanctum'])
def test_scene_is_opt_in_and_reopens_in_ordinary_engine(scene):
    normal = PlaygroundEngine([5] * 4, encounter=scene).new_run(seed=42)
    e = PlaygroundEngine([5] * 4, encounter=scene, tactical=True)
    s = e.new_run(seed=42)
    assert 'playground' not in normal
    assert s['content_version'] == '0.9.0-tactical-lab'
    assert Engine().view(json.loads(json.dumps(s))) == e.view(s)
    assert 'rng_state' not in e.view(s)
    assert 'base_hazards' not in e.view(s)
    assert len(s['actors']) == len(normal['actors'])


def test_sluice_preview_is_pure_toggle_is_atomic_and_restores_ground():
    e, s = arena()
    s['actors']['P1']['position'] = 'D6'
    s['actors']['P2']['position'] = 'H5'
    s['actors']['E1']['position'] = 'H7'
    c = dict(type='interact', actor='P1', object='sluice')
    before = deepcopy(s)
    assert e.preview(s, c) == e.preview(s, c)
    assert s == before
    opened, _ = e.execute(s, c)
    assert opened['actors']['P1']['budgets']['action'] == 0
    assert opened['actors']['P2']['hp'] == s['actors']['P2']['hp'] - 6
    assert opened['actors']['E1']['hp'] == 994
    assert opened['map']['hazards']['H6']['damage'] == 6
    with pytest.raises(GameError):
        e.execute(opened, c)
    opened['actors']['P3']['position'] = 'E5'
    closed, _ = e.execute(opened, dict(type='interact', actor='P3', object='sluice'))
    assert closed['map']['hazards']['H6']['damage'] == 3
    assert 'H5' not in closed['map']['hazards']
    assert not closed['playground']['objects']['sluice']['active']


@pytest.mark.parametrize('change', [dict(object='bogus'), dict(object=[]), dict(extra=True), dict(actor='E1')])
def test_invalid_interaction_cannot_spend_or_roll(change):
    e, s = arena()
    before = deepcopy(s)
    with pytest.raises(GameError):
        e.execute(s, dict(type='interact', actor='P1', object='sluice') | change)
    assert s == before


def test_interaction_requires_adjacent_visible_active_actor_and_action():
    e, s = arena()
    c = dict(type='interact', actor='P1', object='sluice')
    with pytest.raises(GameError, match='距离'):
        e.preview(s, c)
    s['actors']['P1']['position'] = 'D6'
    s['map']['walls'] = ['E6']
    with pytest.raises(GameError):
        e.preview(s, c)
    s['map']['walls'] = []
    s['pending'] = {'kind': 'opportunity'}
    with pytest.raises(GameError):
        e.preview(s, c)


@pytest.mark.parametrize('disabled', ['none', 'stunned', 'reaction', 'distance', 'wall', 'dead'])
def test_guard_consumes_reaction_and_has_real_counterplay(disabled):
    e, s = arena('archive')
    guard, target = s['actors']['E1'], s['actors']['E4']
    guard['position'], target['position'] = 'J6', 'L6'
    if disabled == 'stunned': guard['conditions'] = ['stunned']
    if disabled == 'reaction': guard['budgets']['reaction'] = 0
    if disabled == 'distance': guard['position'] = 'H6'
    if disabled == 'wall': s['map']['walls'] = ['K6']
    if disabled == 'dead': guard.update(hp=0, conditions=['dead'])
    e._damage(s, target, 10, 'force', '测试', source_actor='P3')
    assert target['hp'] == (996 if disabled == 'none' else 990)
    e._damage(s, target, 10, 'force', '测试', source_actor='P3')
    assert target['hp'] == (986 if disabled == 'none' else 980)


def test_hidden_guard_is_not_named_in_target_preview_or_damage():
    e, s = arena('archive')
    s['actors']['E1'].update(position='J6', hidden=True)
    s['actors']['E4']['position'] = 'K6'
    s['actors']['P3']['position'] = 'F6'
    assert not e._visible(s, s['actors']['E1'])
    c = dict(type='cast', actor='P3', ability='magic_missile', target='E4')
    assert 'E1' not in json.dumps(e.preview(s, c), ensure_ascii=False)
    after, events = e.execute(s, c)
    assert 'E1' not in json.dumps(events, ensure_ascii=False)
    assert after['actors']['E1']['budgets']['reaction'] == 0


def test_shelter_is_symmetric_and_destroying_it_changes_real_damage():
    e, s = arena('sanctum')
    s['actors']['P1']['position'] = 'G9'
    s['actors']['E1']['position'] = 'I9'
    for aid in ('P1', 'E1'):
        hp = s['actors'][aid]['hp']
        e._damage(s, s['actors'][aid], 10, 'fire', '测试')
        assert s['actors'][aid]['hp'] == hp - 7
    c = dict(type='interact', actor='P1', object='shelter')
    after, _ = e.execute(s, c)
    hp = after['actors']['E1']['hp']
    e._damage(after, after['actors']['E1'], 10, 'fire', '测试')
    assert after['actors']['E1']['hp'] == hp - 10
    with pytest.raises(GameError): e.preview(after, c)
    assert '已摧毁' in render_map(e.view(after))


def test_ember_weapon_bonus_is_real_and_requires_line_of_sight():
    e, s = arena('sanctum')
    s['actors']['P1']['position'] = 'G3'
    s['actors']['E1']['position'] = 'G4'
    c = dict(type='attack', actor='P1', target='E1')
    assert any('额外2' in w for w in e.preview(s, c)['warnings'])
    baseline = deepcopy(s)
    baseline['playground']['objects']['ember']['active'] = False
    with patch('core.dice.die', side_effect=lambda state, sides: min(10, sides)):
        with_aura, _ = e.execute(s, c)
        without, _ = e.execute(baseline, c)
    assert with_aura['actors']['E1']['hp'] == without['actors']['E1']['hp'] - 2
    s['actors']['P1']['position'] = 'F3'
    s['map']['walls'] = ['G3']
    assert not e._aura(s, s['actors']['P1'], 'ember')


def test_intercept_stops_pending_movement_but_leaves_attack_budget():
    e, s = arena()
    s['actors']['P2']['position'] = 'A1'
    mover = s['actors']['E1']
    e._continue_move(s, mover, ['E4', 'F4'])
    assert s['pending']['reactor'] == 'P1'
    assert '截击盾' in str(e.view(s)['pending']['preview'])
    with patch('core.dice.die', side_effect=lambda state, sides: min(10, sides)):
        after, events = e.react(s, 'accept')
    assert after['pending'] is None
    assert after['actors']['E1']['position'] == 'E4'
    assert after['actors']['E1']['budgets']['movement'] == 0
    assert after['actors']['E1']['budgets']['action'] == 1
    assert any(x['details'].get('trait') == 'intercept' for x in events)
    declined, _ = e.react(s, 'decline')
    assert declined['actors']['E1']['position'] == 'F4'
    assert declined['actors']['P1']['budgets']['reaction'] == 1


def test_withdraw_requires_successful_sneak_is_once_and_keeps_normal_movement():
    e, s = arena()
    c = dict(type='withdraw', actor='P2', destination='B4')
    with pytest.raises(GameError): e.preview(s, c)
    with patch('core.dice.die', side_effect=lambda state, sides: min(10, sides)):
        s, _ = e.execute(s, dict(type='attack', actor='P2', target='E1'))
    assert s['actors']['P2']['withdraw_ready']
    s['actors']['P2']['budgets']['movement'] = 0
    before = deepcopy(s)
    p = e.preview(s, c)
    assert s == before and p['cost'] == {} and not p.get('opportunity_attacks')
    after, events = e.execute(s, c)
    assert after['actors']['P2']['position'] == 'B4'
    assert after['actors']['P2']['budgets']['movement'] == 0
    assert after['actors']['E1']['budgets']['reaction'] == 1
    assert not any(x['kind'] == 'attack' for x in events)
    with pytest.raises(GameError): e.preview(after, c)
    e._start_actor(s, s['actors']['P2'])
    assert not s['actors']['P2'].get('withdraw_ready')


def test_withdraw_does_not_skip_hazards_or_allow_more_than_three_metres():
    e, s = arena()
    s['actors']['P2']['withdraw_ready'] = True
    s['map']['hazards']['B4'] = dict(damage=3, description='火焰')
    c = dict(type='withdraw', actor='P2', destination='B4')
    assert e.preview(s, c)['hazards']
    after, _ = e.execute(s, c)
    assert after['actors']['P2']['hp'] == s['actors']['P2']['hp'] - 3
    with pytest.raises(GameError): e.preview(s, dict(c, destination='A6'))


def test_actual_healing_grants_one_nonrefreshing_expiring_ward():
    e, s = arena()
    target = s['actors']['P1']
    target['hp'] -= 20
    c = dict(type='cast', actor='P4', ability='healing_word', target='P1')
    after, _ = e.execute(s, c)
    target = after['actors']['P1']
    assert target['healing_ward'] == {'source': 'P4'}
    hp = target['hp']
    e._damage(after, target, 10, 'force', '测试')
    assert target['hp'] == hp - 7 and 'healing_ward' not in target
    e._damage(after, target, 10, 'force', '测试')
    assert target['hp'] == hp - 17
    e._heal(after, target, 1, '治愈真言', source_actor='P4')
    e._start_actor(after, after['actors']['P4'])
    assert 'healing_ward' not in target


@pytest.mark.parametrize('source,actor,target', [('治愈真言','P4','P4'), ('救助','P4','P1'), ('治疗药水','P4','P1'), ('援助术','P4','P1'), ('治愈真言','P3','P1')])
def test_ward_does_not_trigger_for_self_items_help_or_unequipped(source, actor, target):
    e, s = arena()
    s['actors'][target]['hp'] -= 10
    e._heal(s, s['actors'][target], 1, source, source_actor=actor)
    assert 'healing_ward' not in s['actors'][target]


def test_full_health_zero_damage_and_existing_ward_are_not_consumed_or_refreshed():
    e, s = arena()
    target = s['actors']['P1']
    e._heal(s, target, 5, '治愈真言', source_actor='P4')
    assert 'healing_ward' not in target
    target['healing_ward'] = {'source': 'P3'}
    target['hp'] -= 10
    e._heal(s, target, 5, '治愈真言', source_actor='P4')
    assert target['healing_ward']['source'] == 'P3'
    e._damage(s, target, 0, 'force', '测试')
    assert 'healing_ward' in target


def test_content_rejects_unknown_traits_or_wrong_slots():
    for value in ('bogus', [], 'withdraw'):
        data = deepcopy(DATA)
        data['items']['intercept_shield']['tactical_trait'] = value
        with pytest.raises(ValueError, match='tactical'):
            validate_growth(data)


def test_sqlite_reopen_replay_and_menu_cancel_confirm(tmp_path):
    e, s = arena()
    s['actors']['P1']['position'] = 'D6'
    class Fixture(Engine):
        def new_run(self, *args, **kwargs): return deepcopy(s)
    path = tmp_path / 'lab.sqlite3'
    store = Repository(path, Fixture())
    output = []
    client = Client(LocalTransport(repository=store), output=output.append)
    client.create()
    client.ui.select_actor('P1')
    entry = next(x for x in client.view['party'][0]['menu_actions'] if x['command']['type'] == 'interact')
    before = store._load(client.run_id)
    client.ui.select_action(entry)
    client.handle('0')
    assert store._load(client.run_id) == before
    client.ui.select_action(entry)
    client.handle('c')
    rid = client.run_id
    expected = client.view
    assert expected['tactical_objects']['sluice']['active']
    store.close()
    with Repository(path) as reopened:
        assert reopened.get_view(rid) == expected
        # A second real command also survives idempotent receipt replay.
        state = reopened._load(rid)
        cmd = dict(type='move', actor='P2', destination='C4')
        receipt = reopened.submit(rid, 'move-once', state['revision'], cmd)
    with Repository(path) as reopened:
        assert reopened.submit(rid, 'move-once', state['revision'], cmd) == receipt


def test_retry_and_practice_reset_objects_but_preserve_experimental_rules_and_gear():
    e = PlaygroundEngine([5]*4, [dict(type='equip', actor='P1', slot='off_hand', item='intercept_shield')], encounter='foundry', tactical=True)
    s = e.new_run(seed=42)
    s['playground']['objects']['sluice']['active'] = True
    s['phase'] = 'defeated'
    retried, _ = Engine().retry(s)
    assert not retried['playground']['objects']['sluice']['active']
    s['phase'] = 'victory'
    fresh, _ = Engine().execute(s, dict(type='practice'))
    assert fresh['playground']['scene'] == 'foundry'
    assert not fresh['playground']['objects']['sluice']['active']
    assert fresh['actors']['P1']['equipment']['off_hand'] == 'intercept_shield'
    assert fresh['content_version'] == '0.9.0-tactical-lab'


@pytest.mark.parametrize('dimensions', [(110, 36), (132, 44), (160, 50)])
def test_tactical_cards_fit_and_withdraw_uses_real_menu(tmp_path, dimensions):
    e, s = arena('sanctum')
    s['actors']['P2']['withdraw_ready'] = True
    class Fixture(Engine):
        def new_run(self, *args, **kwargs): return deepcopy(s)
    with Repository(tmp_path / 'cards.sqlite3', Fixture()) as store:
        screen = DecisionScreen(io.StringIO())
        client = Client(LocalTransport(repository=store), output=screen.message, presenter=screen.present)
        screen.client = client
        client.create()
        client.ui.select_actor('P2')
        entry = next(x for x in client.view['party'][1]['menu_actions'] if x['command']['type'] == 'withdraw')
        client.ui.select_action(entry)
        assert client.ui.stage == 'move'
        client.handle('b4')
        assert client.ui.stage == 'confirm'
        before = store._load(client.run_id)
        frame = screen.frame(dimensions)
        assert len(frame.splitlines()) <= dimensions[1]
        assert all(columns(line) <= dimensions[0] for line in frame.splitlines())
        client.handle('0')
        assert store._load(client.run_id) == before
        client.handle('b4')
        client.handle('c')
        assert next(a for a in client.view['party'] if a['id'] == 'P2')['position'] == 'B4'


def test_whole_experimental_battle_is_identical_after_every_sqlite_reopen(tmp_path):
    from tools.audit_tactical import choose_mechanism
    e = PlaygroundEngine([3]*4, [dict(type='equip', actor='P2', slot='feet', item='withdrawal_boots')], encounter='foundry', tactical=True)
    initial = e.new_run(templates=PARTY, seed=7)
    class Fixture(Engine):
        def new_run(self, *args, **kwargs): return deepcopy(initial)
    path = tmp_path / 'whole.sqlite3'
    with Repository(path, Fixture()) as store:
        rid = store.create_run(command_id='create')['run_id']
    direct, used = initial, set()
    for index in range(200):
        if direct['phase'] != 'combat': break
        op, command = choose_mechanism(e, direct, used)
        previous = direct
        direct, events = e.react(direct, command) if op == 'reaction' else e.execute(direct, command)
        with Repository(path) as store:
            if op == 'reaction':
                receipt = store.react(rid, str(index), previous['revision'], command)
            else:
                receipt = store.submit(rid, str(index), previous['revision'], command)
            assert store._load(rid) == direct
            assert receipt['events'] == events
        if isinstance(command, dict) and command['type'] == 'interact': used.add(command['object'])
    assert direct['phase'] == 'victory'
    assert 'sluice' in used
