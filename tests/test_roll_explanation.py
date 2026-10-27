"""Visible roll explanations must agree with actual dice selection and costs."""

from copy import deepcopy
from unittest.mock import patch

import pytest

from client.render import render_preview, render_result
from core.engine import Engine


@pytest.fixture
def arena():
    engine = Engine()
    state = engine.new_run(seed=42, levels=[5] * 4)
    state['map'].update(walls=[], heights={}, difficult=[], hazards={})
    positions = dict(P1='B2', P2='B3', P3='B6', P4='B7', E1='C2', E2='H6', E3='I6')
    for aid, actor in state['actors'].items():
        actor.update(position=positions[aid], ended=False, conditions=[], effects={})
        actor['budgets'] = dict(action=1, bonus=1, reaction=1, movement=actor['speed'])
        if actor['side'] == 'enemy':
            actor.update(hp=1000, max_hp=1000)
    state.update(active_group=['P1', 'P2', 'P3', 'P4'], cursor=4,
                 turn_order=['P1', 'P2', 'P3', 'P4', 'E1', 'E2', 'E3'])
    return engine, state


@pytest.mark.parametrize('target_conditions,attacker_conditions,label,selection,expected_rolls,reason', [
    ([], [], '普通掷骰', '取 7', [7], ''),
    (['stunned'], [], '优势', '取高 8', [7, 8], '目标被震慑'),
    (['dodge'], [], '劣势', '取低 7', [7, 8], '目标正在闪避'),
    (['stunned'], ['prone'], '优势与劣势抵消', '取 7', [7], '攻击者伏地'),
])
def test_preview_and_real_roll_explain_mode_and_selected_die(
        arena, target_conditions, attacker_conditions, label, selection, expected_rolls, reason):
    engine, state = arena
    state['actors']['E1']['conditions'] = target_conditions
    state['actors']['P1']['conditions'] = attacker_conditions
    command = dict(type='attack', actor='P1', target='E1')
    original = deepcopy(state)
    preview = engine.preview(state, command)
    assert state == original  # Explanations and probabilities never roll or spend resources.
    text = render_preview(1, preview, state['revision'], engine.view(state))
    assert label in text and reason in text
    with patch('core.dice.die', side_effect=[7, 8] + [1] * 20):
        changed, events = engine.execute(state, command)
    attack = next(event for event in events if event['kind'] == 'attack')
    assert attack['details']['rolls'] == expected_rolls
    assert attack['details']['natural'] == (8 if selection == '取高 8' else 7)
    result = render_result(events, engine.view(state), engine.view(changed))
    assert label in result and selection in result and reason in result
    if label == '优势与劣势抵消':
        assert '优势来源：目标被震慑' in result
        assert '劣势来源：攻击者伏地' in result


def test_scorching_ray_explains_each_target_and_magic_missile_does_not_roll(arena):
    engine, state = arena
    state['actors']['E2']['conditions'] = ['stunned']
    state['actors']['E3']['conditions'] = ['dodge']
    command = dict(type='cast', actor='P3', ability='scorching_ray', targets=['E2', 'E3', 'E2'])
    preview = engine.preview(state, command)
    text = render_preview(1, preview, state['revision'], engine.view(state))
    assert '掷2次d20取高' in text and '掷2次d20取低' in text
    with patch('core.dice.die', side_effect=lambda s, n: 7 if n == 20 else 1):
        _, events = engine.execute(state, command)
    attacks = [event for event in events if event['kind'] == 'attack']
    assert [event['details']['advantage'] for event in attacks] == [True, False, True]
    assert [event['details']['disadvantage'] for event in attacks] == [False, True, False]
    missile = engine.preview(state, dict(type='cast', actor='P3', ability='magic_missile', target='E3'))
    assert 'attack_roll' not in missile
    assert all('attack_roll' not in part for part in missile['allocations'])


def test_flurry_fire_bolt_and_opportunity_previews_include_the_roll_reason(arena):
    engine, state = arena
    state['actors']['E1']['conditions'] = ['stunned']
    for command in (dict(type='ability', actor='P2', ability='flurry', target='E1'),
                    dict(type='cast', actor='P3', ability='fire_bolt', target='E1')):
        assert engine.preview(state, command)['attack_roll']['advantage_reasons'] == ['目标被震慑']
    state['pending'] = dict(kind='opportunity', reactor='P1', mover='E1')
    pending = engine.view(state)['pending']['preview']
    assert '目标被震慑' in render_preview('反应', pending, state['revision'])


def test_enemy_attack_and_dodge_save_show_selected_die(arena):
    engine, state = arena
    state['actors']['P1']['conditions'] = ['dodge']
    with patch('core.dice.die', side_effect=[17, 3] + [1] * 10):
        engine._weapon_attack(state, state['actors']['E1'], state['actors']['P1'])
    attack = next(event for event in state['event_log'] if event['kind'] == 'attack')
    assert '劣势' in attack['text'] and '取低 3' in attack['text']
    assert '目标正在闪避' in attack['text']
    with patch('core.dice.die', side_effect=[7, 18]):
        engine._save(state, state['actors']['P1'], 'dex', 15, '测试')
    save = state['event_log'][-1]
    assert '优势' in save['text'] and '取高 18' in save['text']
    assert '闪避使敏捷豁免获得优势' in save['text']


def test_untrained_shield_and_ranged_threat_reasons_are_visible(arena):
    engine, state = arena
    wizard = state['actors']['P3']
    wizard['equipment']['off_hand'] = 'shield'
    info = engine._attack_roll_info(state, wizard, state['actors']['E2'], ranged=True)
    assert info['disadvantage_reasons'] == ['护甲或盾牌未熟练']
    wizard['equipment']['off_hand'] = None
    state['actors']['E3']['position'] = 'C6'
    preview = engine.preview(state, dict(type='cast', actor='P3', ability='fire_bolt', target='E2'))
    assert preview['attack_roll']['disadvantage_reasons'] == ['远程攻击时有敌人贴身']
