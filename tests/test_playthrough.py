import json
import uuid

import pytest

from saves.storage import Repository
from tools.play_slice import play


@pytest.mark.parametrize('templates,seed', [
    (['fighter','monk','wizard','cleric'],42),
    (['fighter','fighter','monk','monk'],713),
    (['wizard','wizard','cleric','cleric'],2026),
])
def test_complete_slice_with_three_party_types(templates, seed):
    state, summary = play(templates, seed)
    assert summary['outcome'] == 'victory', summary
    assert summary['commands'] < 600
    assert state['revision'] == summary['commands']


def test_full_playthrough_survives_every_write_restart_and_replay(tmp_path):
    database = tmp_path / 'game.sqlite3'
    repository = Repository(database)
    creation = repository.create_run(seed=42, command_id='create')
    run_id = creation['run_id']
    repository.close()

    def check(previous, operation, payload, changed, events):
        # The core smoke run has a fresh UUID; the database owns its own ID.
        with Repository(database) as repo:
            command_id = str(uuid.uuid4())
            if operation == 'reaction':
                receipt = repo.react(run_id, command_id, previous['revision'], payload)
                replay = repo.react(run_id, command_id, previous['revision'], payload)
            else:
                receipt = repo.submit(run_id, command_id, previous['revision'], payload)
                replay = repo.submit(run_id, command_id, previous['revision'], payload)
            assert receipt == replay
            expected = repo.engine.view(changed)
            expected['id'] = run_id
            assert receipt['view'] == expected
            assert receipt['events'] == events
    _, summary = play(seed=42, on_step=check)
    assert summary['outcome'] == 'victory'
    with Repository(database) as repo:
        assert repo.get_view(run_id)['outcome'] == 'victory'
        event_page = repo.events(run_id, limit=200)
        assert event_page['events']
        assert 'rng_state' not in json.dumps(event_page)
