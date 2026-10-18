"""Explicit GitHub save upload/download on the separate game-saves branch."""

from contextlib import closing
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import uuid

from saves.locking import database_lock
from saves.storage import backup_database

ROOT = Path(__file__).resolve().parents[1]
BRANCH = 'game-saves'


class SyncError(Exception):
    pass


def git(directory, *args, allowed=(0,)):
    environment = dict(os.environ, GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='never')
    try:
        result = subprocess.run(['git', '-C', str(directory), *args],
                                capture_output=True, text=True, encoding='utf-8',
                                errors='replace', env=environment, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SyncError('Git 无法运行或连接超时；请检查 Git 安装、网络和 GitHub 登录。') from exc
    if result.returncode not in allowed:
        # Do not echo URLs/credential-helper output which may contain secrets.
        raise SyncError('Git 操作失败（%s）；请检查 GitHub 权限和网络。若推送冲突，请先下载远端存档。' % args[0])
    return result


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def fingerprint(database):
    """Validate a game database and hash logical rows, independent of WAL/layout."""
    path = Path(database).resolve()
    if not path.is_file():
        raise SyncError('找不到本地存档；请先开始游戏，或使用 pull 下载存档。')
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as connection:
        if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise SyncError('存档完整性检查失败。')
        if connection.execute('PRAGMA user_version').fetchone()[0] != 1:
            raise SyncError('存档格式版本不兼容，请更新游戏代码。')
        digest = hashlib.sha256()
        for table in ('runs', 'events', 'receipts'):
            rows = connection.execute('SELECT * FROM ' + table).fetchall()
            encoded = sorted(json.dumps(row, ensure_ascii=False, separators=(',', ':')) for row in rows)
            digest.update(json.dumps([table, encoded], ensure_ascii=False).encode('utf-8'))
        # Also validate rule/content compatibility before replacing a local save.
        from core.engine import Engine, GameError
        engine = Engine()
        for (state,) in connection.execute('SELECT state_json FROM runs'):
            try:
                engine.view(json.loads(state))
            except GameError as exc:
                raise SyncError('存档规则或内容不兼容，请更新代码：%s' % exc.message) from exc
            except (KeyError, TypeError, AttributeError, IndexError) as exc:
                raise SyncError('存档游戏状态格式损坏。') from exc
        return digest.hexdigest()


def read_json(path):
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise SyncError('存档同步记录格式错误，应为 JSON 对象。')
    return value


def sync(action, *, database='data/game.sqlite3', config='data/client.json',
         project=ROOT, replace=False, output=print):
    project = Path(project).resolve()
    database, config = Path(database).resolve(), Path(config).resolve()
    journal = config.with_name(config.stem + '.pending.json')
    state_path = Path(str(database) + '.github.json')
    remote = git(project, 'remote', 'get-url', 'origin').stdout.strip()
    # Store only the identity hash locally, never a potentially credentialed URL.
    identity = hashlib.sha256(remote.encode()).hexdigest()
    with database_lock(database), tempfile.TemporaryDirectory(prefix='CmdRPG-save-') as temporary:
        if journal.exists():
            raise SyncError('有未确认的本地操作，请先启动游戏恢复回执、退出后再同步。')
        work = Path(temporary)
        git(work, 'init', '-q', '-b', BRANCH)
        git(work, 'remote', 'add', 'origin', remote)
        remote_ref = git(work, 'ls-remote', '--exit-code', '--heads', 'origin',
                         'refs/heads/' + BRANCH, allowed=(0, 2))
        exists = remote_ref.returncode == 0
        head, metadata, remote_digest = None, {}, None
        if exists:
            git(work, 'fetch', '-q', '--depth=1', 'origin', 'refs/heads/' + BRANCH)
            git(work, 'checkout', '-q', '-B', BRANCH, 'FETCH_HEAD')
            head = git(work, 'rev-parse', 'HEAD').stdout.strip()
            metadata = read_json(work / 'save.json')
            if metadata.get('format') != 1:
                raise SyncError('GitHub 存档清单版本不兼容。')
            remote_digest = fingerprint(work / 'game.sqlite3')
            if metadata.get('fingerprint') != remote_digest:
                raise SyncError('GitHub 存档校验失败，未修改本地存档。')
            with closing(sqlite3.connect(work / 'game.sqlite3')) as connection:
                if metadata.get('run_id') is not None and not connection.execute(
                    'SELECT 1 FROM runs WHERE id = ?', (metadata['run_id'],)
                ).fetchone():
                    raise SyncError('GitHub 存档中的所选周目不存在。')
        local_digest = fingerprint(database) if database.exists() else None
        known = read_json(state_path) if state_path.exists() else {}
        if known.get('remote') != identity:
            known = {}
        if action == 'push':
            if local_digest is None:
                raise SyncError('没有可上传的本地存档。')
            if exists and remote_digest == local_digest:
                atomic_json(state_path, dict(remote=identity, head=head, fingerprint=local_digest))
                output('GitHub 已是同一份存档，无需上传。')
                return
            if exists and known.get('head') != head and not replace:
                raise SyncError('GitHub 有本机未同步的存档，已停止上传。请先 pull；确需用本机覆盖时加 --replace。')
            backup_database(database, work / ('snapshot.sqlite3' if exists else 'game.sqlite3'))
            if exists:
                os.replace(work / 'snapshot.sqlite3', work / 'game.sqlite3')
            last = read_json(config).get('last', {}) if config.exists() else {}
            # Machine-specific paths and credentials never enter the save branch.
            run_id = last.get('run_id') if last.get('mode') == 'local' and Path(last.get('db', '')).resolve() == database else None
            if not run_id:
                with closing(sqlite3.connect(work / 'game.sqlite3')) as connection:
                    row = connection.execute('SELECT id FROM runs ORDER BY updated_at DESC LIMIT 1').fetchone()
                    run_id = row[0] if row else None
            atomic_json(work / 'save.json', dict(format=1, fingerprint=local_digest,
                        run_id=run_id, saved_at=datetime.now(timezone.utc).isoformat()))
            git(work, 'add', '--', 'game.sqlite3', 'save.json')
            git(work, '-c', 'user.name=CmdRPG Saves', '-c', 'user.email=cmdrpg-saves@users.noreply.github.com',
                'commit', '-q', '-m', 'Save local game progress')
            # Normal fast-forward push also protects against a concurrent upload.
            git(work, 'push', '-q', 'origin', 'HEAD:refs/heads/' + BRANCH)
            head = git(work, 'rev-parse', 'HEAD').stdout.strip()
            atomic_json(state_path, dict(remote=identity, head=head, fingerprint=local_digest))
            output('存档已上传 GitHub（game-saves 分支）。')
            return
        if not exists:
            raise SyncError('GitHub 还没有存档，请先在有进度的电脑执行 push。')
        if local_digest and local_digest != remote_digest:
            if local_digest != known.get('fingerprint') and not replace:
                raise SyncError('本机有尚未上传的进度，已停止下载覆盖。请先 push；确需采用远端时加 --replace，原档会备份。')
            backup = database.parent / 'save-backups' / (datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:8] + '.sqlite3')
            backup_database(database, backup)
            output('原本机存档已备份：%s' % backup)
        if local_digest != remote_digest:
            # Stage beside the destination so os.replace stays on one filesystem.
            stage = database.with_name(database.name + '.' + uuid.uuid4().hex + '.tmp')
            try:
                shutil.copyfile(work / 'game.sqlite3', stage)
                # Close/checkpoint any committed WAL before replacing its database.
                if database.exists():
                    with closing(sqlite3.connect(database, timeout=0)) as connection:
                        connection.execute('BEGIN EXCLUSIVE')
                        connection.rollback()
                        result = connection.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
                        if result[0]:
                            raise SyncError('存档仍被其他程序占用，请关闭后重试。')
                    for suffix in ('-wal', '-shm'):
                        Path(str(database) + suffix).unlink(missing_ok=True)
                os.replace(stage, database)
            finally:
                stage.unlink(missing_ok=True)
        atomic_json(config, {'last': dict(mode='local', db=str(database), run_id=metadata.get('run_id'))})
        atomic_json(state_path, dict(remote=identity, head=head, fingerprint=remote_digest))
        output('已下载 GitHub 存档，下次启动将继续所选周目。')


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='手动上传/下载 GitHub 存档；请先退出游戏。')
    parser.add_argument('action', choices=('push', 'pull'))
    parser.add_argument('--db', default=os.environ.get('CMDRPG_DB', 'data/game.sqlite3'))
    parser.add_argument('--config', default=os.environ.get('CMDRPG_CLIENT_CONFIG', 'data/client.json'))
    parser.add_argument('--replace', action='store_true', help='明确采用本次来源覆盖目标进度；下载前仍备份本机存档')
    args = parser.parse_args(argv)
    try:
        sync(args.action, database=args.db, config=args.config, replace=args.replace)
    except (SyncError, OSError, sqlite3.Error, ValueError, RuntimeError) as exc:
        print('存档同步失败：%s' % exc)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
