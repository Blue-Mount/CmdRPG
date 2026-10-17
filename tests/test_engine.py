from copy import deepcopy
import json
from unittest.mock import patch

import pytest

from core.engine import Engine, GameError
from core import dice, spatial


@pytest.fixture
def engine():
    return Engine()


@pytest.fixture
def state(engine):
    state = engine.new_run(seed=42)
    state['map'].update(walls=[], heights={}, difficult=[], hazards={})
    positions = {'P1':'B4','P2':'B5','P3':'B6','P4':'B7','E1':'C4','E2':'L2','E3':'N2'}
    for aid, actor in state['actors'].items():
        actor['position'] = positions[aid]
        actor['ended'] = False
        actor['budgets'] = {'action':1,'bonus':1,'reaction':1,'movement':9}
        actor['hp'] = actor['max_hp']
    state['turn_order'] = ['P1','P2','P3','P4','E1','E2','E3']
    state['active_group'] = ['P1','P2','P3','P4']
    state['cursor'] = 4
    return state


def test_preview_is_pure(engine, state):
    before = deepcopy(state)
    for _ in range(3):
        view = engine.view(state)
        preview = engine.preview(state, {'type':'attack','actor':'P1','target':'E1'})
        assert 0 < preview['hit_probability'] < 1
        assert 'rng_state' not in view
    assert state == before


@pytest.mark.parametrize('command', [
    {'type':'attack','actor':'P1','target':'E3'},
    {'type':'cast','actor':'P1','ability':'fire_bolt','target':'E1'},
    {'type':'cast','actor':'P3','ability':'magic_missile','target':'E1','slot':True},
    {'type':'cast','actor':'P4','ability':'bless','targets':['P1','P1']},
    {'type':'move','actor':'P1','destination':'Z99'},
    {'type':'cast','actor':'P3','ability':'fire_bolt','target':'E1','slot':1},
    {'type':'attack','actor':'P1','target':'E1','hp':999},
])
def test_invalid_action_is_atomic(engine, state, command):
    before = deepcopy(state)
    with pytest.raises(GameError):
        engine.execute(state, command)
    assert state == before


def test_critical_dice_resistance_and_trace(engine, state):
    state['actors']['E1']['hp'] = state['actors']['E1']['max_hp'] = 100
    state['actors']['E1']['resistances'] = ['slashing']
    with patch('core.dice.die', side_effect=[20, 8, 8]):
        changed, events = engine.execute(state, {'type':'attack','actor':'P1','target':'E1'})
    assert changed['actors']['E1']['hp'] == 91  # floor((2d8 + 3) / 2)
    attack = next(e for e in events if e['kind'] == 'attack')['details']
    damage = next(e for e in events if e['kind'] == 'damage')['details']
    assert attack['critical'] and damage['rolls'] == [8, 8]
    assert damage['ability_modifier'] == 3
    assert changed['actors']['P1']['budgets']['action'] == 0
    assert state['actors']['P1']['budgets']['action'] == 1


def test_hit_probability_advantage_cancels_and_bless_exact():
    assert dice.hit_probability(5, 13, True, True) == dice.hit_probability(5, 13)
    assert dice.hit_probability(100, 1) == .95
    assert dice.hit_probability(-100, 99) == .05
    assert dice.hit_probability(5, 13, bless=True) == .775
    assert dice.hit_probability(5, 13, True) == .8775


def test_monk_bonus_requires_attack_and_spends_once(engine, state):
    state['actors']['E1']['position'] = 'C5'
    state['actors']['E1']['hp'] = state['actors']['E1']['max_hp'] = 100
    command = {'type':'ability','actor':'P2','ability':'unarmed_strike','target':'E1'}
    with pytest.raises(GameError, match='需先'):
        engine.execute(state, command)
    with patch('core.dice.die', side_effect=[10, 2, 10, 2]):
        changed, _ = engine.execute(state, {'type':'attack','actor':'P2','target':'E1'})
        changed, _ = engine.execute(changed, command)
    assert changed['actors']['P2']['budgets']['action'] == 0
    assert changed['actors']['P2']['budgets']['bonus'] == 0
    assert changed['actors']['E1']['hp'] == 90
    with pytest.raises(GameError):
        engine.execute(changed, command)


def test_concentration_replacement_and_damage(engine, state):
    command = {'type':'cast','actor':'P4','ability':'bless','targets':['P1','P4']}
    first, _ = engine.execute(state, command)
    assert first['actors']['P4']['slots']['1'] == 1
    first['actors']['P4']['budgets']['action'] = 1
    preview = engine.preview(first, {**command, 'targets':['P2']})
    assert preview['warnings']
    second, _ = engine.execute(first, {**command, 'targets':['P2']})
    assert 'bless' not in second['actors']['P1']['effects']
    assert 'bless' in second['actors']['P2']['effects']
    with patch('core.dice.die', return_value=1):
        engine._damage(second, second['actors']['P4'], 1, 'fire', '测试伤害')
    assert second['actors']['P4']['concentration'] is None
    assert 'bless' not in second['actors']['P2']['effects']


def test_life_healing_revival_and_same_turn_spells(engine, state):
    target = state['actors']['P1']
    target.update(hp=0, conditions=['downed'], death_failure=2, death_success=1)
    with patch('core.dice.die', return_value=1):
        changed, events = engine.execute(state, {'type':'cast','actor':'P4','ability':'healing_word','target':'P1'})
    assert changed['actors']['P1']['hp'] == 7  # d4=1 + Wis=3 + Life=3
    assert changed['actors']['P1']['conditions'] == []
    assert changed['actors']['P1']['death_failure'] == 0
    assert changed['actors']['P4']['budgets']['action'] == 1
    changed, _ = engine.execute(changed, {'type':'cast','actor':'P4','ability':'bless','targets':['P1','P2']})
    assert changed['actors']['P4']['slots']['1'] == 0
    assert next(e for e in events if e['kind'] == 'heal')['details']['life_domain'] == 3


def test_opportunity_pause_and_resume(engine, state):
    state["actors"]["P2"]["position"] = "A8"
    mover = state['actors']['E1']
    mover['hp'] = mover['max_hp'] = 100
    before = state['rng_state']
    engine._continue_move(state, mover, ['D4', 'E4'])
    assert state['pending']['reactor'] == 'P1'
    assert state['rng_state'] == before and mover['position'] == 'C4'
    restored = json.loads(json.dumps(state))
    with patch('core.dice.die', side_effect=[10, 4]):
        changed, events = engine.react(restored, 'accept')
    assert changed['pending'] is None
    assert changed['actors']['E1']['position'] == 'E4'
    assert changed['actors']['E1']['budgets']['movement'] == 6
    assert changed['actors']['P1']['budgets']['reaction'] == 0
    assert len([e for e in events if e['kind'] == 'attack']) == 1
    assert state['actors']['E1']['position'] == 'C4'


def test_movement_is_preserved_when_opportunity_downs_player(engine, state):
    state['actors']['P1']['position'] = 'A4'
    state['actors']['P1']['hp'] = 1
    command = {'type':'move','actor':'P1','destination':'D3'}
    route = engine.preview(state, command)['path']
    # Use an open row so the first step enters reach and the next leaves.
    state['actors']['P1']['position'] = 'A3'
    state['actors']['E1']['position'] = 'B4'
    with patch('core.dice.die', side_effect=[20, 6, 6]):
        changed, events = engine.execute(state, {'type':'move','actor':'P1','destination':'D3'})
    assert changed['actors']['P1']['position'] == 'C3'
    assert changed['actors']['P1']['budgets']['movement'] == 6
    assert 'downed' in changed['actors']['P1']['conditions']
    assert len([e for e in events if e['kind'] == 'move']) == 2


def test_disengage_prevents_opportunity(engine, state):
    changed, _ = engine.execute(state, {'type':'disengage','actor':'P1'})
    rng = changed['rng_state']
    changed, events = engine.execute(changed, {'type':'move','actor':'P1','destination':'A4'})
    assert changed['rng_state'] == rng
    assert not any(e['kind'] == 'attack' for e in events)


def test_death_saves_and_healing(engine, state):
    actor = state['actors']['P1']
    actor.update(hp=0, conditions=['downed'], death_success=0, death_failure=1)
    with patch('core.dice.die', return_value=1):
        engine._start_actor(state, actor)
    assert 'dead' in actor['conditions']
    actor.update(hp=0, conditions=['downed'], death_failure=0)
    with patch('core.dice.die', return_value=20):
        engine._start_actor(state, actor)
    assert actor['hp'] == 1 and not actor['ended']
    actor.update(hp=0, conditions=['downed'], death_success=2, death_failure=0)
    with patch('core.dice.die', return_value=10):
        engine._start_actor(state, actor)
    assert 'stable' in actor['conditions']


def test_hidden_state_not_in_view_or_preview(engine, state):
    state['actors']['E3']['hidden'] = True
    view = engine.view(state)
    assert 'E3' not in [a['id'] for a in view['enemies']]
    assert not {'rng_state','battle_snapshot','initiative','turn_order'} & set(view)
    with pytest.raises(GameError):
        engine.preview(state, {'type':'cast','actor':'P3','ability':'fire_bolt','target':'E3'})
    before = deepcopy(state)
    engine.view(state)
    assert state == before


def test_hidden_enemy_attack_reveals_before_public_log(engine, state):
    enemy = state['actors']['E3']
    enemy['position'] = 'N4'
    enemy['hidden'] = True
    assert not engine._visible(state, enemy)
    engine._enemy_finish(state, enemy)
    assert engine._visible(state, enemy)
    assert not enemy['hidden']


def test_retry_restores_rng_and_resources(engine):
    state = engine.new_run(seed=712)
    original = deepcopy(state)
    state['phase'] = 'defeated'
    for actor in state['actors'].values():
        if actor['side'] == 'party':
            actor['hp'] = 0
            actor['resources']['potions'] = 0
    state['rng_state'] = 999
    changed, _ = engine.retry(state)
    assert changed['rng_state'] == original['rng_state']
    assert {aid:a['hp'] for aid,a in changed['actors'].items()} == {aid:a['hp'] for aid,a in original['actors'].items()}
    assert changed['actors']['P1']['resources']['potions'] == 2
    assert changed['revision'] == state['revision'] + 1


def test_short_rest_is_limited_and_does_not_restore_spell_slots(engine, state):
    state['phase'] = 'victory'
    state['actors']['P1']['hp'] = 1
    state['actors']['P1']['resources']['second_wind'] = 0
    state['actors']['P3']['slots']['1'] = 0
    changed, _ = engine.execute(state, {'type':'rest','kind':'short'})
    assert changed['actors']['P1']['hp'] == 7
    assert changed['actors']['P1']['resources']['second_wind'] == 1
    assert changed['actors']['P3']['slots']['1'] == 0
    changed, _ = engine.execute(changed, {'type':'rest','kind':'short'})
    with pytest.raises(GameError):
        engine.execute(changed, {'type':'rest','kind':'short'})


def test_full_party_short_rest_preview_matches_one_execution_and_stays_pure(engine, state):
    state.update(phase='victory', outcome='victory')
    state['actors']['P1']['hp'] = 1
    state['actors']['P1']['resources'].update(second_wind=0, potions=0)
    state['actors']['P2'].update(hp=0, conditions=['downed'])
    state['actors']['P3']['hp'] = state['actors']['P3']['max_hp'] - 1
    state['actors']['P3']['slots']['1'] = 0
    state['actors']['P4'].update(hp=0, conditions=['dead'])
    before = deepcopy(state)
    command = {'type':'rest','kind':'short'}
    preview = engine.preview(state, command)
    assert preview == engine.preview(state, command)
    assert preview['cost'] == {'short_rests': 1}
    assert preview['targets'] == ['P1', 'P2', 'P3']
    assert '全队短休' in preview['description'] and '向上取整' in preview['description']
    assert '不会复活' in preview['description'] and '法术位' in preview['description']
    assert next(effect for effect in preview['rest_effects'] if effect['actor'] == 'P1')['resources_restore'] == {'second_wind': 1}
    changed, events = engine.execute(state, command)
    assert changed['short_rests'] == state['short_rests'] - 1
    assert changed['revision'] == state['revision'] + 1
    assert changed['rng_state'] == state['rng_state']
    for effect in preview['rest_effects']:
        member = changed['actors'][effect['actor']]
        assert member['hp'] == effect['hp_after']
        assert effect['healing'] == member['hp'] - state['actors'][effect['actor']]['hp']
        for resource, amount in effect['resources_restore'].items():
            assert member['resources'][resource] == state['actors'][effect['actor']]['resources'][resource] + amount
    assert changed['actors']['P1']['resources']['potions'] == 0
    assert changed['actors']['P3']['slots']['1'] == 0
    assert changed['actors']['P4'] == state['actors']['P4']
    assert 'downed' not in changed['actors']['P2']['conditions']
    assert len([event for event in events if event['kind'] == 'rest']) == 1
    assert len([event for event in events if event['kind'] == 'heal']) == 3
    assert state == before


def test_victory_view_reports_current_slice_settlement_without_rewards(engine, state):
    assert engine.view(state)['settlement'] is None
    state.update(phase='victory', outcome='victory')
    before = deepcopy(state)
    settlement = engine.view(state)['settlement']
    assert settlement['completed'] and settlement['next_encounter'] is None
    assert not settlement['loot_available'] and '没有配置可拾取战利品' in settlement['loot_description']
    settlement['loot_available'] = True
    assert not engine.view(state)['settlement']['loot_available']
    assert state == before


def test_wall_height_and_difficult_path():
    board = {'width':5,'height':5,'cell_size':1.5,'walls':['C2'],'heights':{},'difficult':['B1']}
    assert not spatial.line_of_sight(board, 'A2', 'E2')
    assert spatial.path(board, 'A1', 'C1')[1] == 4.5
    board['heights']['B1'] = 3
    assert 'B1' not in dict(spatial.neighbors(board, 'A1'))
    assert not spatial.line_of_sight(board, 'A1', 'C1')
    assert spatial.distance(board, 'A1','B1') > 3


def test_save_proficiency_and_dodge_advantage(engine, state):
    fighter = state['actors']['P1']
    fighter['save_proficiencies'] = ['str', 'con']
    with patch('core.dice.die', return_value=6):
        assert engine._save(state, fighter, 'con', 10, '专注')
    assert state['event_log'][-1]['details']['proficiency'] == 2
    monk = state['actors']['P2']
    monk['save_proficiencies'] = ['str', 'dex']
    monk['conditions'] = ['dodge']
    with patch('core.dice.die', side_effect=[1, 18]):
        assert engine._save(state, monk, 'dex', 15, '圣火术')
    assert state['event_log'][-1]['details']['rolls'] == [1, 18]
    assert state['event_log'][-1]['details']['advantage']


def test_two_clerics_bless_does_not_stack_or_lose_other_source(engine, state):
    state['actors']['P1']['abilities'] = ['bless']
    state['actors']['P1']['prepared'] = ['bless']  # synthetic second caster must prepare its spell
    state['actors']['P1']['slots'] = {'1':2}
    state['actors']['P1']['spell_stat'] = 'wis'
    state, _ = engine.execute(state, {'type':'cast','actor':'P1','ability':'bless','targets':['P3']})
    state, _ = engine.execute(state, {'type':'cast','actor':'P4','ability':'bless','targets':['P3']})
    assert state['actors']['P3']['effects']['bless']['sources'] == ['P1','P4']
    engine._drop_concentration(state, state['actors']['P4'], '测试终止')
    assert engine._blessed(state['actors']['P3'])
    assert state['actors']['P3']['effects']['bless']['sources'] == ['P1']
    with patch('core.dice.die', side_effect=[10, 4, 3]) as die:
        engine._attack(state, state['actors']['P3'], state['actors']['E1'], '1d10', 'fire', 'int', ranged=True, damage_modifier=0)
    assert die.call_args_list[1].args[1] == 4
    assert die.call_count == 3  # one d20, one bless die, one damage die


def test_lowercase_coordinates_cannot_walk_through_walls(engine, state):
    board = state['map']
    board['walls'] = ['B2']
    assert not spatial.walkable(board, 'b2')
    assert spatial.path(board, 'b2','B3') is None
    with pytest.raises(GameError):
        engine.preview(state, {'type':'move','actor':'P1','destination':'b2'})


def test_enemy_paths_around_visible_downed_player(engine, state):
    enemy = state['actors']['E1']
    enemy['position'] = 'A2'
    state['actors']['P1'].update(position='B2', hp=0, conditions=['downed'])
    state['actors']['P2']['position'] = 'E2'
    state['actors']['P3']['position'] = 'P11'
    state['actors']['P4']['position'] = 'P12'
    state['actors']['E2']['position'] = 'N12'
    state['actors']['E3']['position'] = 'O12'
    with patch('core.dice.die', return_value=1):
        engine._enemy_turn(state, enemy)
    assert enemy['position'] == 'D1'
    assert enemy['budgets']['movement'] == 3
    assert enemy['budgets']['action'] == 0
