"""Combat accounting uses actual observed results and survives save operations."""

from copy import deepcopy
from unittest.mock import patch

import pytest

from client.cards import DecisionScreen
from client.cli import Client
from client.render import render_battle_report
from client.transport import LocalTransport
from core.engine import Engine
from saves.storage import Repository
from tools.play_slice import choose


@pytest.fixture
def arena():
    engine = Engine()
    state = engine.new_run(seed=42, levels=[8] * 4)
    state['map'].update(walls=[], heights={}, hazards={}, difficult=[])
    for aid, pos in dict(P1='B4', P2='B5', P3='B6', P4='B7', E1='C4', E2='L2', E3='N2').items():
        actor = state['actors'][aid]
        actor.update(position=pos, hp=actor['max_hp'], conditions=[], effects={}, ended=False)
        actor['budgets'] = dict(action=1, bonus=1, reaction=1, movement=actor['speed'])
        if actor['side'] == 'enemy':
            actor.update(hp=1000, max_hp=1000)
    state.update(active_group=['P1', 'P2', 'P3', 'P4'], cursor=4,
                 turn_order=['P1', 'P2', 'P3', 'P4', 'E1', 'E2', 'E3'])
    return engine, state


def test_actual_hp_loss_absorption_resistance_death_ward_and_overhealing(arena):
    engine, state = arena
    enemy = state['actors']['E1']
    enemy.update(hp=3, temp_hp=5, resistances=['fire'])
    engine._damage(state, enemy, 100, 'fire', '测试', source_actor='P3')
    report = state['battle_report']
    assert report['enemy_hp_lost'] == report['members']['P3']['damage'] == 3
    member = state['actors']['P1']
    member.update(hp=4, temp_hp=3, effects={'death_ward': {'source': 'P4'}})
    engine._damage(state, member, 100, 'fire', '测试')
    assert member['hp'] == 1
    assert report['party_hp_lost'] == report['members']['P1']['taken'] == 3
    assert report['absorbed'] == report['members']['P1']['absorbed'] == 3
    engine._heal(state, member, 1000, '测试', source_actor='P4')
    assert report['healing'] == report['members']['P4']['healing'] == member['max_hp'] - 1
    before = deepcopy(report)
    engine._heal(state, member, 1000, '测试', source_actor='P4')
    assert report == before


def test_hidden_damage_cannot_leak_through_aggregate_or_member_report(arena):
    engine, state = arena
    enemy = state['actors']['E2']
    enemy.update(position='N10', hidden=True)
    assert not engine._visible(state, enemy)
    before = deepcopy(state['battle_report'])
    engine._damage(state, enemy, 12, 'force', '测试', source_actor='P3')
    assert enemy['hp'] == 988
    assert state['battle_report'] == before


def test_multihit_spell_actual_output_and_slots_are_not_double_counted(arena):
    engine, state = arena
    state['actors']['E1']['hp'] = 1
    cmd = dict(type='cast', actor='P3', ability='magic_missile', targets=['E1', 'E1', 'E2'], slot=1)
    with patch('core.dice.die', return_value=2):
        state, events = engine.execute(state, cmd)
    row = state['battle_report']['members']['P3']
    assert row['damage'] == 4  # 1 actual HP, skipped segment, then 3 HP.
    assert row['costs'] == {'slot_1': 1}
    assert row['attacks'] == 0
    assert any(e['kind'] == 'attack_skip' for e in events)


def test_aoe_sculpt_and_friendly_damage_accounting(arena):
    engine, state = arena
    state['actors']['E1']['position'] = 'D6'
    before = {aid: a['hp'] for aid, a in state['actors'].items()}
    with patch('core.dice.die', return_value=1):
        state, _ = engine.execute(state, dict(type='cast', actor='P3', ability='fireball', destination='D6', slot=3))
    enemy_lost = sum(before[aid] - a['hp'] for aid, a in state['actors'].items() if a['side'] == 'enemy')
    party_lost = sum(before[aid] - a['hp'] for aid, a in state['actors'].items() if a['side'] == 'party')
    report = state['battle_report']
    assert party_lost == 0  # Evocation sculpting protects allies.
    assert report['enemy_hp_lost'] == report['members']['P3']['damage'] == enemy_lost
    assert report['party_hp_lost'] == party_lost
    engine._damage(state, state['actors']['P1'], 5, 'fire', '友方伤害', source_actor='P3')
    assert report['party_hp_lost'] == 5
    assert report['members']['P3']['damage'] == enemy_lost


@pytest.mark.parametrize('roll,ki,hits', [(1, 0, 0), (20, 1, 1)])
def test_conditional_ki_and_attack_roll_accounting(arena, roll, ki, hits):
    engine, state = arena
    with patch('core.dice.die', return_value=roll):
        state, _ = engine.execute(state, dict(type='ability', actor='P2', ability='stunning_strike', target='E1'))
    row = state['battle_report']['members']['P2']
    assert row['attacks'] == 1 and row['hits'] == hits and row['criticals'] == hits
    assert row['costs'].get('ki', 0) == ki
    assert state['actors']['P2']['max_resources']['ki'] - state['actors']['P2']['resources']['ki'] == ki


def test_real_healing_attributes_capped_healing_and_self_passive(arena):
    engine, state = arena
    state['actors']['P1']['hp'] -= 2
    state['actors']['P4']['hp'] -= 1
    with patch('core.dice.die', return_value=4):
        state, _ = engine.execute(state, dict(type='cast', actor='P4', ability='healing_word', target='P1', slot=1))
    report = state['battle_report']
    assert report['healing'] == report['members']['P4']['healing'] == 3
    assert report['members']['P4']['costs'] == {'slot_1': 1}


def test_rest_and_log_truncation_preserve_combat_totals(arena):
    engine, state = arena
    engine._damage(state, state['actors']['P1'], 5, 'fire', '环境')
    engine._heal(state, state['actors']['P1'], 1, '测试', source_actor='P4')
    for _ in range(120):
        engine._emit(state, 'effect', '测试事件')
    assert len(state['event_log']) == 100
    before = deepcopy(state['battle_report'])
    legacy = deepcopy(state['battle_summary'])
    state.update(phase='victory', outcome='victory', active_group=[])
    state, _ = engine.execute(state, dict(type='rest', kind='short'))
    assert state['battle_report'] == before
    assert state['battle_summary'] == legacy


def test_old_battle_stays_explicitly_unrecorded_without_mutating_view(arena):
    engine, state = arena
    del state['battle_report']
    before = deepcopy(state)
    assert '旧存档' in render_battle_report(engine.view(state))
    assert state == before
    state, _ = engine.execute(state, dict(type='attack', actor='P1', target='E1'))
    assert 'battle_report' not in state


def test_sqlite_replay_restart_retry_and_read_only_report(tmp_path):
    path = tmp_path / 'report.sqlite3'
    with Repository(path) as repo:
        created = repo.create_run(seed=42, command_id='create')
        run_id = created['run_id']
        previous = repo._load(run_id)
        engine = repo.engine
        for index in range(50):
            operation, payload = choose(engine, previous)
            if operation == 'reaction':
                receipt = repo.react(run_id, str(index), previous['revision'], payload)
                assert repo.react(run_id, str(index), previous['revision'], payload) == receipt
            else:
                receipt = repo.submit(run_id, str(index), previous['revision'], payload)
                assert repo.submit(run_id, str(index), previous['revision'], payload) == receipt
            previous = repo._load(run_id)
            if previous['battle_report']['enemy_hp_lost']:
                break
        assert previous['battle_report']['enemy_hp_lost'] > 0
        expected = deepcopy(previous['battle_report'])
    with Repository(path) as repo:
        assert repo.get_view(run_id)['battle_report'] == expected
        output = []
        client = Client(LocalTransport(repository=repo), run_id=run_id, view=repo.get_view(run_id), output=output.append)
        before = repo._load(run_id)
        client.handle('report')
        assert '对敌有效伤害' in '\n'.join(output)
        assert repo._load(run_id) == before
        before['phase'] = 'defeated'
        restored, _ = repo.engine.retry(before)
        assert restored['battle_report'] == repo.engine.new_run(seed=42)['battle_report']


def test_new_encounter_resets_report_and_settlement_menu_exposes_it(tmp_path):
    from tools.play_campaign import play

    checked = []

    def checkpoint(old, operation, payload, state, events):
        if old['phase'] == 'victory' and payload.get('type') == 'camp':
            assert state['battle_report'] == old['battle_report']
        if old['phase'] == 'victory' and payload.get('type') == 'travel':
            # Opening enemy actions, if any, legitimately belong to the next battle.
            assert state['battle_report']['rounds'] == 1
            assert all(row['damage'] == 0 and row['costs'] == {} for row in state['battle_report']['members'].values())
            checked.append(state['campaign']['node'])

    state, report = play(seed=42, on_step=checkpoint)
    assert len(checked) == 13 and report['completed']
    assert all(b['report']['enemy_hp_lost'] > 0 for b in report['battles'])

    class Won(Engine):
        def new_run(self, *args, **kwargs):
            return deepcopy(state)

    with Repository(tmp_path / 'won.sqlite3', engine=Won()) as repo:
        output = []
        client = Client(LocalTransport(repository=repo), output=output.append)
        client.create(seed=42)
        before = repo._load(client.run_id)
        assert '18 查看本场战报' in client.ui.render()
        client.handle('18')
        assert '实际治疗' in '\n'.join(output)
        assert repo._load(client.run_id) == before
        screen = DecisionScreen()
        screen.client = client
        frame = screen.frame((110, 36))
        assert '决策' in frame


@pytest.mark.parametrize('natural,changed', [(1, False), (8, True), (15, False), (20, False)])
def test_bless_credit_uses_actual_roll_excluding_natural_one_and_twenty(arena, natural, changed):
    engine, state = arena
    actor = state['actors']['P1']
    actor['effects']['bless'] = {'sources': ['P4'], 'source': 'P4'}
    bonus = engine.view(state)['party'][0]['combat_stats']['attack_bonus']
    state['actors']['E1']['ac'] = bonus + 10
    before = deepcopy(state)
    engine.preview(state, dict(type='attack', actor='P1', target='E1'))
    assert state == before
    with patch('core.dice.die', side_effect=lambda s, n: natural if n == 20 else 3):
        state, events = engine.execute(state, dict(type='attack', actor='P1', target='E1'))
    event = next(e for e in events if e['kind'] == 'attack')
    assert event['details']['bless_changed'] == changed
    assert ('祝福促成命中' in event['text']) == changed
    assert state['battle_report']['support']['bless_hits'] == int(changed)


def test_bless_save_and_concentration_credit_counts_one_event_for_shared_sources(arena):
    engine, state = arena
    actor = state['actors']['P1']
    actor['effects']['bless'] = {'sources': ['P3', 'P4'], 'source': 'P4'}
    actor['concentration'] = dict(ability='bless', targets=['P1'], remaining=10)
    # CON +2 without proficiency for a deliberately controlled threshold.
    actor['stats']['con'] = 14
    actor['save_proficiencies'] = []
    with patch('core.dice.die', side_effect=lambda s, n: 5 if n == 20 else 3):
        engine._damage(state, actor, 1, 'fire', '测试')  # 5+2+3 succeeds at DC10.
    report = state['battle_report']['support']
    assert actor['concentration'] is not None
    assert report['bless_saves'] == 1
    assert '祝福促成豁免成功' in state['event_log'][-1]['text']
    with patch('core.dice.die', side_effect=lambda s, n: 15 if n == 20 else 3):
        assert engine._save(state, actor, 'con', 10, '测试')
    assert report['bless_saves'] == 1  # A save that already succeeds gives no credit.


def test_successful_stun_records_real_skipped_turns_and_automatic_saves(arena):
    engine, state = arena
    with patch('core.dice.die', side_effect=lambda s, n: 10 if n == 20 else 1):
        state, _ = engine.execute(state, dict(type='ability', actor='P2', ability='stunning_strike', target='E1'))
    enemy = state['actors']['E1']
    assert 'stunned' in enemy['conditions']
    assert state['battle_report']['support']['stunned_turns'] == 0
    engine._start_actor(state, enemy)
    assert enemy['ended'] and enemy['budgets']['action'] == 0
    assert state['battle_report']['support']['stunned_turns'] == 1
    with patch('core.dice.die', side_effect=AssertionError('Automatic failure must not roll')):
        assert not engine._save(state, enemy, 'dex', 16, '圣火术')
    assert state['battle_report']['support']['automatic_failures'] == 1
    text = render_battle_report(engine.view(state))
    assert '震慑阻止敌方回合 1次' in text
    assert '豁免自动失败 1次' in text
    before = deepcopy(state)
    render_battle_report(engine.view(state))
    assert state == before


def test_hidden_control_events_cannot_inflate_public_support_report(arena):
    engine, state = arena
    enemy = state['actors']['E2']
    enemy.update(position='N10', hidden=True, conditions=['stunned'])
    before = deepcopy(state['battle_report'])
    engine._start_actor(state, enemy)
    assert not engine._save(state, enemy, 'dex', 16, '测试')
    assert state['battle_report'] == before


def test_preexisting_report_without_support_is_not_partially_backfilled(arena):
    engine, state = arena
    del state['battle_report']['support']
    actor = state['actors']['P1']
    actor['effects']['bless'] = {'source': 'P4'}
    with patch('core.dice.die', return_value=8):
        state, _ = engine.execute(state, dict(type='attack', actor='P1', target='E1'))
    assert 'support' not in state['battle_report']
    assert '尚未记录辅助收益' in render_battle_report(engine.view(state))


def test_nonzero_support_counters_survive_sqlite_restart_and_receipt_replay(arena, tmp_path):
    engine, state = arena
    state['actors']['P1']['effects']['bless'] = {'source': 'P4'}
    state['actors']['E1']['ac'] = engine.view(state)['party'][0]['combat_stats']['attack_bonus'] + 10

    class Blessed(Engine):
        def new_run(self, *args, **kwargs):
            return deepcopy(state)

    path = tmp_path / 'blessed.sqlite3'
    with Repository(path, engine=Blessed()) as repo:
        created = repo.create_run(seed=42, command_id='create')
        run_id = created['run_id']
        command = dict(type='attack', actor='P1', target='E1')
        with patch('core.dice.die', side_effect=lambda s, n: 8 if n == 20 else 3):
            receipt = repo.submit(run_id, 'attack', state['revision'], command)
        assert receipt['view']['battle_report']['support']['bless_hits'] == 1
    with Repository(path) as repo:
        assert repo.submit(run_id, 'attack', state['revision'], command) == receipt
        assert repo.get_view(run_id)['battle_report']['support']['bless_hits'] == 1
