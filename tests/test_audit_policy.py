"""Smoke policy coverage must reflect legal reach and real attack budgets."""

from copy import deepcopy
from unittest.mock import patch

import pytest

from core.engine import Engine
from tools.play_slice import choose, choose_support, move_for_attack
from tools.audit_campaign import audit


@pytest.fixture
def arena():
    engine = Engine()
    state = engine.new_run(seed=42, levels=[8] * 4)
    state['map'].update(walls=[], heights={}, hazards={}, difficult=[])
    for aid, pos in dict(P1='B4', P2='B5', P3='B6', P4='B7', E1='D4', E2='L2', E3='N2').items():
        actor = state['actors'][aid]
        actor.update(position=pos, conditions=[], effects={}, ended=False)
        actor['budgets'] = dict(action=1, bonus=1, reaction=1, movement=actor['speed'])
        if actor['side'] == 'enemy':
            actor.update(hp=1000, max_hp=1000)
    state.update(active_group=['P1', 'P2', 'P3', 'P4'], cursor=4,
                 turn_order=['P1', 'P2', 'P3', 'P4', 'E1', 'E2', 'E3'])
    return engine, state


def test_melee_path_uses_diagonal_reach_without_changing_ranged_distance(arena):
    engine, state = arena
    state['actors']['P1']['position'] = 'B2'
    state['actors']['E1']['position'] = 'D3'
    view = engine.view(state)
    actor = view['party'][0]
    command = move_for_attack(engine, state, view, actor, [view['enemies'][0]], 1.5)
    assert command['destination'] == 'C2'  # One step then legal diagonal melee.
    assert engine.preview(state, command)['cost']['movement'] == 1.5
    moved, _ = engine.execute(state, command)
    engine.preview(moved, dict(type='attack', actor='P1', target='E1'))
    command = move_for_attack(engine, state, view, actor, [view['enemies'][0]], 3)
    assert command['destination'] == 'B3'  # Ranged geometry still uses 3D distance.


def test_extra_attack_can_reposition_after_action_is_spent(arena):
    engine, state = arena
    state['actors']['P1']['budgets']['action'] = 0
    state['actors']['P1']['extra_attacks'] = 1
    state['actors']['P1']['resources']['action_surge'] = 0
    state['actors']['P1']['resources']['second_wind'] = 0
    state['actors']['P1']['hp'] = state['actors']['P1']['max_hp']
    previous = deepcopy(state)
    op, command = choose(engine, state)
    assert state == previous
    assert op == 'command' and command['type'] == 'move'
    moved, _ = engine.execute(state, command)
    _, command = choose(engine, moved)
    assert command['type'] == 'attack'
    engine.preview(moved, command)


def test_support_policy_uses_only_legal_bless_and_stun_commands(arena):
    engine, state = arena
    state['active_group'] = ['P4', 'P1', 'P2', 'P3']
    _, command = choose_support(engine, state)
    assert command['ability'] == 'bless'
    assert len(command['targets']) == 3
    state, _ = engine.execute(state, command)
    state['active_group'] = ['P2', 'P1', 'P3', 'P4']
    state['actors']['E1']['position'] = 'C5'
    _, command = choose_support(engine, state)
    assert command['ability'] == 'stunning_strike'
    engine.preview(state, command)
    state['actors']['E1']['hidden'] = True
    state['actors']['E1']['position'] = 'N10'
    _, command = choose_support(engine, state)
    assert command.get('target') != 'E1'
    engine.preview(state, command)


def test_audit_keeps_policy_labels_and_errors_in_separate_cases():
    with patch('tools.audit_campaign.play', side_effect=[RuntimeError('at crossfire, round 10'),
                                                        (None, dict(completed=True, seed=42, route='road'))]):
        reports = []
        result = audit(seeds=[42], routes=['road'], parties=['balanced'],
                       policies=['offense', 'support'], on_run=reports.append)
    assert result['total'] == 2 and result['completed'] == 1
    assert [r['policy'] for r in reports] == ['offense', 'support']
    assert result['runs'][0]['error'] == 'at crossfire, round 10'


def test_audit_reports_long_battles_and_observed_casualties():
    from tools.audit_campaign import diagnostics
    result=diagnostics([dict(party='rogues',route='road',seed=42,battles=[
        dict(node='heart',rounds=23,activity={'unused_action_ends':0},report={'downed':2,'deaths':1})])])
    assert result['battles']==1 and result['max_rounds']==23 and result['deaths']==1
    assert result['nodes']['heart']['median']==23
    assert result['suspect_stalls'][0]['unused_action_ends']==0  # A flag is not proof of a deadlock.


def test_shopping_policy_equips_the_bought_rapier_before_travel():
    from tools.play_campaign import play
    def checkpoint(old,operation,payload,state,events):
        if isinstance(payload,dict) and payload.get('type')=='travel':
            assert state['actors']['P1']['equipment']['main_hand']=='rapier'
            assert 'rapier' in state['actors']['P1']['inventory']
            raise StopIteration('first equipment checkpoint complete')
    with pytest.raises(StopIteration):
        play(['rogue']*4,seed=42,shopping=True,on_step=checkpoint)
