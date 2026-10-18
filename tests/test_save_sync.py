"""Two installations exchange real SQLite saves through a real Git remote."""

import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import uuid

import pytest

from client.cli import main as play
from saves.locking import database_lock
from saves.storage import Repository
from tools.sync_saves import SyncError, fingerprint, sync


def command(directory, *args):
    return subprocess.run(['git', '-C', str(directory), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def installations(tmp_path):
    remote = tmp_path / 'remote.git'
    remote.mkdir()
    command(remote, 'init', '--bare', '-q', '-b', 'main')
    machines = []
    for name in ('home', 'away'):
        root = tmp_path / name
        root.mkdir()
        command(root, 'init', '-q', '-b', 'work-in-progress')
        command(root, 'remote', 'add', 'origin', str(remote))
        (root / 'uncommitted.txt').write_text('Keep my development changes')
        machines.append(dict(project=root, database=root/'data/game.sqlite3', config=root/'data/client.json', output=lambda _: None))
    return remote, *machines


def create(machine):
    with Repository(machine['database']) as repository:
        receipt = repository.create_run('测试队伍', seed=42, command_id=str(uuid.uuid4()), campaign=True)
    machine['config'].write_text(json.dumps({'last': dict(mode='local', db=str(machine['database']), run_id=receipt['run_id'])}))
    return receipt['run_id']


def advance(machine, run):
    with Repository(machine['database']) as repository:
        view = repository.get_view(run)
        repository.submit(run, str(uuid.uuid4()), view['revision'], {'type': 'end', 'actor': view['active'][0]})


def test_roundtrip_resume_and_dirty_code_are_preserved(installations):
    remote, home, away = installations
    run = create(home)
    advance(home, run)
    before = command(home['project'], 'status', '--short')
    sync('push', **home)
    assert command(home['project'], 'branch', '--show-current') == 'work-in-progress'
    assert before == command(home['project'], 'status', '--short')
    assert command(remote, 'ls-tree', '--name-only', 'game-saves').splitlines() == ['game.sqlite3', 'save.json']
    assert command(remote, 'show-ref', '--heads').split()[1] == 'refs/heads/game-saves'
    sync('pull', **away)
    assert fingerprint(home['database']) == fingerprint(away['database'])
    assert play(['--db', str(away['database']), '--config', str(away['config'])], input_reader=lambda _: 'q', output=lambda _:None) == 0
    config = json.loads(away['config'].read_text())
    assert config['last']['run_id'] == run
    assert config['last']['db'] == str(away['database'])
    advance(away, run)
    sync('push', **away)
    sync('pull', **home)
    assert fingerprint(home['database']) == fingerprint(away['database'])
    assert list((home['database'].parent/'save-backups').glob('*.sqlite3'))
    with Repository(home['database']) as repository:
        assert repository.get_view(run)['revision'] == 2


def test_divergent_progress_refused_and_explicit_replace_backs_up(installations):
    _, home, away = installations
    run = create(home)
    sync('push', **home)
    sync('pull', **away)
    advance(home, run)
    sync('push', **home)
    advance(away, run)
    prior = fingerprint(away['database'])
    with pytest.raises(SyncError, match='停止上传'):
        sync('push', **away)
    with pytest.raises(SyncError, match='停止下载'):
        sync('pull', **away)
    assert fingerprint(away['database']) == prior
    sync('pull', replace=True, **away)
    backups = list((away['database'].parent/'save-backups').glob('*.sqlite3'))
    assert len(backups) == 1 and fingerprint(backups[0]) == prior
    assert fingerprint(away['database']) == fingerprint(home['database'])


def test_unknown_local_progress_and_pending_operation_are_not_overwritten(installations):
    _, home, away = installations
    create(home)
    create(away)
    sync('push', **home)
    with pytest.raises(SyncError, match='停止下载'):
        sync('pull', **away)
    journal = home['config'].with_name('client.pending.json')
    journal.write_text('{}')
    with pytest.raises(SyncError, match='未确认'):
        sync('push', **home)


def test_game_and_sync_share_cross_process_lock(installations):
    _, home, _ = installations
    create(home)
    with database_lock(home['database']):
        process = subprocess.run([sys.executable, '-c',
            'from saves.locking import database_lock; import sys; '
            'database_lock(sys.argv[1]).__enter__()', str(home['database'])],
            capture_output=True, text=True, encoding='utf-8', errors='replace')
        assert process.returncode != 0
        with pytest.raises(OSError, match='正在游玩'):
            sync('push', **home)
        output = []
        assert play(['--db', str(home['database']), '--config', str(home['config'])], output=output.append) == 1
        assert '正在游玩' in output[-1]
    sync('push', **home)


def test_missing_remote_save_and_failed_auth_leave_local_save(installations):
    _, home, away = installations
    with pytest.raises(SyncError, match='还没有存档'):
        sync('pull', **away)
    assert not away['database'].exists()
    create(home)
    prior = fingerprint(home['database'])
    command(home['project'], 'remote', 'set-url', 'origin', str(home['project']/'missing.git'))
    with pytest.raises(SyncError, match='Git 操作失败'):
        sync('push', **home)
    assert fingerprint(home['database']) == prior


def test_corrupted_remote_refused_before_local_changes(installations):
    remote, home, away = installations
    create(home)
    sync('push', **home)
    poison = home['project']/'poison'
    command(home['project'], 'clone', '-q', '-b', 'game-saves', str(remote), str(poison))
    (poison/'game.sqlite3').write_bytes(b'broken SQLite')
    command(poison, 'add', 'game.sqlite3')
    command(poison, '-c', 'user.name=test', '-c', 'user.email=test@example.com', 'commit', '-qm', 'Corrupt test data')
    command(poison, 'push', '-q', 'origin', 'game-saves')
    with pytest.raises(sqlite3.DatabaseError):
        sync('pull', **away)
    assert not away['database'].exists() and not away['config'].exists()
