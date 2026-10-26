"""Audit both routes' settlement and player choices independently of combat AI."""

from copy import deepcopy

import pytest

from client.cli import Client
from client.transport import LocalTransport
from core.engine import Engine, GameError
from saves.storage import Repository


def settle(engine, state):
    # Controlled victory fixture; real combat paths are covered in test_campaign.
    for actor in state['actors'].values():
        if actor['side'] == 'enemy':
            actor.update(hp=0, conditions=['dead'])
    engine._outcome(state)


@pytest.mark.parametrize('route', ['road', 'ridge'])
def test_each_settlement_preserves_build_choices_and_spent_capacity(route):
    engine = Engine()
    state = engine.new_run(seed=42, campaign=True)
    for stage, node in enumerate(['outpost', route, 'gate', 'forge', 'heart'], 1):
        assert state['campaign']['node'] == node
        party = [a for a in state['actors'].values() if a['side'] == 'party']
        for actor in party:
            actor['hp'] = actor['max_hp'] - 3
            actor['slots'] = {k: v - 1 for k, v in actor['max_slots'].items()}
            actor['resources'].update({k: v - 1 for k, v in actor['max_resources'].items()})
            actor['resources']['potions'] = 1
        before = deepcopy(state)
        settle(engine, state)
        level = [2,4,6,8,8][stage - 1]
        assert state['campaign']['gold'] == before['campaign']['gold'] + 60 + 20 * stage
        assert engine.view(state)['settlement']['loot_available'] is False
        for aid in ['P1', 'P2', 'P3', 'P4']:
            actor, old = state['actors'][aid], before['actors'][aid]
            assert actor['level'] == level
            assert actor['max_hp'] - actor['hp'] == 3
            reward = engine.campaign_content['nodes'][node].get('equipment_reward', {}).get(actor['template'])
            expected_inventory = old['inventory'] + ([reward] if reward and reward not in old['inventory'] else [])
            assert actor['inventory'] == expected_inventory
            assert actor['equipment'] == old['equipment']
            assert actor['resources']['potions'] == 1
            for current, maximum in [('slots', 'max_slots'), ('resources', 'max_resources')]:
                for key, capacity in actor[maximum].items():
                    spent = old[maximum].get(key, 0) - old[current].get(key, 0)
                    assert actor[current][key] == capacity - spent
            with pytest.raises(GameError):
                engine.execute(state, {'type': 'levelup', 'actor': aid})
        fighter, wizard = state['actors']['P1'], state['actors']['P3']
        assert fighter['stats']['str'] == (20 if level >= 6 else 18 if level >= 4 else 16)
        assert fighter['extra_attack'] == int(level >= 5)
        assert ('scorching_ray' in wizard['known_spells']) == (level >= 3)
        assert ('fireball' in wizard['known_spells']) == (level >= 5)
        if stage == 1:
            state, _ = engine.execute(state, {'type': 'buy', 'actor': 'P1', 'item': 'greatsword'})
            assert state['actors']['P1']['equipment']['main_hand'] == 'longsword'
            state, _ = engine.execute(state, {'type': 'equip', 'actor': 'P1', 'slot': 'off_hand', 'item': None})
            state, _ = engine.execute(state, {'type': 'equip', 'actor': 'P1', 'slot': 'main_hand', 'item': 'greatsword'})
            state, _ = engine.execute(state, {'type': 'prepare', 'actor': 'P3', 'spells': ['mage_armor']})
        else:
            assert fighter['equipment']['main_hand'] == 'greatsword'
            assert 'magic_missile' not in wizard['prepared']
            assert 'scorching_ray' in wizard['prepared']
            assert ('fireball' in wizard['prepared']) == (level >= 5)
        settled = deepcopy(state)
        engine._campaign_reward(state)
        assert state == settled
        if stage < 5:
            state, _ = engine.execute(state, {'type': 'camp'})
            destination = route if stage == 1 else engine.view(state)['campaign']['routes'][0]['id']
            state, _ = engine.execute(state, {'type': 'travel', 'destination': destination})
    assert state['campaign']['completed']


def test_campaign_spell_menu_cancel_confirm_and_domain_restriction(tmp_path):
    engine = Engine()
    state = engine.new_run(seed=42, campaign=True)
    settle(engine, state)

    class SettledEngine(Engine):
        def new_run(self, *args, **kwargs):
            return deepcopy(state)

    store = Repository(tmp_path / 'post-battle.sqlite3', engine=SettledEngine())
    client = Client(LocalTransport(repository=store), output=lambda _: None)
    try:
        client.create(campaign=True)
        client.handle('9')  # P3: wizard growth and equipment.
        entries = client.ui.build()
        key = next(k for k, (kind, entry) in entries.items()
                   if kind == 'manage' and entry[0] == '取消准备 魔法飞弹')
        before = deepcopy(client.view)
        client.handle(key)
        client.handle('0')
        assert client.view == before
        client.handle(key)
        client.handle('c')
        assert 'magic_missile' not in client.view['party'][2]['prepared']
        with pytest.raises(GameError):
            engine.execute(state, {'type': 'prepare', 'actor': 'P4', 'spells': []})
    finally:
        store.close()
