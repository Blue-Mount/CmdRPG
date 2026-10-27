"""M4 real battle paths, atomic economics, recovery and playable menus."""

from copy import deepcopy
import json
from pathlib import Path
import uuid
import os
import subprocess
import sys

import pytest

from client.cli import Client
from client.transport import LocalTransport
from core.campaign import load_campaign
from core.engine import Engine, GameError
from saves.storage import Repository
from tools.play_campaign import play


def victory():
    engine = Engine()
    state = engine.new_run(seed=42, campaign=True)
    for actor in state['actors'].values():
        if actor['side'] == 'enemy':
            actor.update(hp=0, conditions=['dead'])
    engine._outcome(state)
    return engine, state


@pytest.mark.parametrize('party', [None, ['fighter','fighter','monk','monk'], ['wizard','cleric','wizard','cleric']])
@pytest.mark.parametrize('route', ['road', 'ridge'])
def test_seven_real_battles_to_boss(party, route):
    state, report = play(party, seed=42, route=route)
    assert report['completed'], report
    assert [b['node'] for b in report['battles']] == ['outpost', route, 'gate', 'forge', 'crossfire', 'crucible', 'heart']
    assert [b['levels'] for b in report['battles']] == [[2]*4, [4]*4, [6]*4, [8]*4, [8]*4, [8]*4, [8]*4]
    assert all(b['outcome'] == 'victory' for b in report['battles'])
    assert len(state['campaign']['cleared']) == 7
    assert any(e['kind'] == 'campaign_complete' for e in state['event_log'])


def test_rewards_previews_and_upgrade_gates_are_pure_and_not_repeatable():
    engine, state = victory()
    assert state['campaign']['gold'] == 120
    before = deepcopy(state)
    engine._outcome(state)
    engine._campaign_reward(state)
    assert state == before
    for cmd in ({'type':'camp'}, {'type':'travel','destination':'ridge'},
                {'type':'buy','actor':'P1','item':'greatsword'}):
        assert engine.preview(state, cmd)['description']
        assert state == before
    for cmd in ({'type':'travel','destination':'heart'}, {'type':'travel','destination':[]},
                {'type': []}, {'type': {}},
                {'type':'buy','actor':'E1','item':'greatsword'}, {'type':'buy','actor':'P1','item':'shield'},
                {'type':'levelup','actor':'P1'}, {'type':'camp','extra':True}):
        with pytest.raises(GameError):
            engine.execute(state, cmd)
        assert state == before


def test_camp_purchase_equip_and_next_map_preserve_loadout_and_consumption():
    engine, state = victory()
    member = state['actors']['P1']
    member.update(hp=0, conditions=['dead'], death_failure=3)
    with pytest.raises(GameError):
        engine.preview(state, {'type':'travel','destination':'road'})
    state, _ = engine.execute(state, {'type':'camp'})
    assert state['campaign']['gold'] == 100
    assert state['actors']['P1']['hp'] == state['actors']['P1']['max_hp']
    assert state['actors']['P1']['conditions'] == []
    with pytest.raises(GameError):
        engine.execute(state, {'type':'camp'})
    state, _ = engine.execute(state, {'type':'buy','actor':'P1','item':'greatsword'})
    state, _ = engine.execute(state, {'type':'equip','actor':'P1','slot':'off_hand','item':None})
    state, _ = engine.execute(state, {'type':'equip','actor':'P1','slot':'main_hand','item':'greatsword'})
    state['actors']['P3']['slots']['1'] -= 1
    state['actors']['P1']['resources']['potions'] -= 1
    party = deepcopy({k: v for k, v in state['actors'].items() if v['side'] == 'party'})
    state, _ = engine.execute(state, {'type':'travel','destination':'ridge'})
    assert state['campaign']['node'] == 'ridge'
    assert state['campaign']['gold'] == 65
    assert state['actors']['P1']['equipment']['main_hand'] == 'greatsword'
    assert state['actors']['P1']['resources']['potions'] == party['P1']['resources']['potions']
    assert state['actors']['P3']['slots'] == party['P3']['slots']
    assert state['map']['heights']['K4'] == 3
    assert state['battle_snapshot']['campaign']['node'] == 'ridge'
    # Defeat and retry restore this encounter, its seed and economy, not stage one.
    state['phase'] = 'defeated'
    state['campaign']['gold'] = 0
    restored, _ = engine.retry(state)
    assert restored['campaign']['gold'] == 65
    assert restored['campaign']['node'] == 'ridge'
    assert restored['actors']['P1']['equipment']['main_hand'] == 'greatsword'


def test_campaign_survives_database_reopen_and_receipt_replay_every_write(tmp_path):
    path = tmp_path / 'campaign.sqlite3'
    store = Repository(path)
    created = store.create_run(seed=42, campaign=True, command_id='create')
    run_id = created['run_id']
    engine = Engine()

    def checkpoint(old, operation, payload, expected, events):
        nonlocal store
        cid = str(uuid.uuid4())
        method = store.react if operation == 'reaction' else store.submit
        receipt = method(run_id, cid, old['revision'], payload)
        assert method(run_id, cid, old['revision'], payload) == receipt
        expected_view = engine.view(expected)
        expected_view['id'] = run_id
        assert receipt['view'] == expected_view
        assert receipt['events'] == events
        store.close()
        store = Repository(path)
        assert store.get_view(run_id) == expected_view
        assert store._load(run_id)['rng_state'] == expected['rng_state']

    try:
        _, report = play(seed=42, on_step=checkpoint)
        assert report['completed']
        assert store.get_view(run_id)['campaign']['completed']
    finally:
        store.close()


def test_campaign_menu_new_run_cancel_shop_and_travel(tmp_path):
    engine, state = victory()
    # Seed a controlled safe phase; all following writes use the real client and DB.
    class SafeEngine(Engine):
        def new_run(self, *args, **kwargs):
            return deepcopy(state)
    store = Repository(tmp_path / 'menu.sqlite3', engine=SafeEngine())
    output = []
    client = Client(LocalTransport(repository=store), output=output.append)
    client.create(campaign=True)
    try:
        assert '没有下一关' not in '\n'.join(output)
        client.handle('16'); client.handle('1')
        assert client.ui.stage == 'shop'
        greatsword = str(next(n for n, item in enumerate(client.view['campaign']['shop'], 1) if item['id'] == 'greatsword'))
        client.handle(greatsword)
        gold = client.view['campaign']['gold']
        client.handle('0')
        assert client.view['campaign']['gold'] == gold
        client.handle(greatsword); client.handle('c')
        assert 'greatsword' in client.view['party'][0]['inventory']
        assert client.view['campaign']['gold'] == gold - 35
        client.handle('15'); client.handle('c')
        assert client.view['campaign']['node'] in client.view['campaign']['camped']
        client.handle('14'); client.handle('c')
        assert client.view['campaign']['node'] == 'ridge'
        assert client.view['phase'] == 'combat'
        assert any('北侧山脊' in line for line in output)
    finally:
        store.close()


def test_start_campaign_from_home_preserves_original_run_and_cancel(tmp_path):
    store = Repository(tmp_path / 'start.sqlite3')
    client = Client(LocalTransport(repository=store), output=lambda _: None)
    client.create(seed=42)
    original = client.run_id
    original_view = deepcopy(client.view)
    try:
        client.handle('9'); client.handle('0')
        assert client.ui.stage == 'home'
        assert client.run_id == original
        client.handle('9'); client.handle('c')
        assert client.run_id != original
        assert client.view['campaign']['stage'] == 1
        assert store.get_view(original) == original_view
    finally:
        store.close()


def test_campaign_real_terminal_start_resume_and_isolated_playground(tmp_path):
    database, config = tmp_path / 'cli.sqlite3', tmp_path / 'cli.json'
    common = ['--db', str(database), '--config', str(config)]
    kwargs = dict(input='q\n', capture_output=True, text=True, encoding='utf-8', timeout=30,
                  creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    first = subprocess.run([sys.executable, '-m', 'client', '--campaign', '--seed', '42', *common], **kwargs)
    assert first.returncode == 0, first.stderr
    assert '第1/7站' in first.stdout and '裂隙远征' in first.stdout
    assert '没有下一关' not in first.stdout
    resumed = subprocess.run([sys.executable, '-m', 'client', *common], **kwargs)
    assert resumed.returncode == 0, resumed.stderr
    assert '第1/7站' in resumed.stdout
    isolated = subprocess.run([sys.executable, '-m', 'tools.dev_play', '--campaign'], **kwargs)
    assert isolated.returncode == 0, isolated.stderr
    assert 'CmdRPG-M4-' in isolated.stdout and '第1/7站' in isolated.stdout




def test_campaign_save_version_is_distinct_from_m3_and_rejects_wrong_versions():
    engine = Engine()
    ordinary = engine.new_run(seed=42)
    campaign = engine.new_run(seed=42, campaign=True)
    assert ordinary['content_version'] == '0.6.0-m6'
    assert campaign['content_version'] == '0.6.0-campaign'
    for field, value in [('version', 99), ('content_version', '0.3.0-m3')]:
        invalid = deepcopy(campaign)
        if field == 'version': invalid['campaign']['version'] = value
        else: invalid[field] = value
        with pytest.raises(GameError, match='版本'):
            engine.view(invalid)


@pytest.mark.parametrize('mutation', ['route', 'wall', 'reward', 'shop', 'version'])
def test_campaign_rejects_broken_content(tmp_path, mutation):
    content = load_campaign()
    if mutation == 'route': content['nodes']['outpost']['next'] = ['heart']
    elif mutation == 'wall': content['nodes']['outpost']['map']['walls'].append('B5')
    elif mutation == 'reward': content['nodes']['gate']['gold'] = -1
    elif mutation == 'shop': content['shop']['imaginary_item'] = 10
    else: content['version'] = 99
    path = tmp_path / 'invalid.json'
    path.write_text(json.dumps(content, ensure_ascii=False), encoding='utf-8')
    with pytest.raises(ValueError): load_campaign(path)
