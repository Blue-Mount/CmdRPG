"""Actual release payloads, TCP downloads, process locks, migration and game recovery."""
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
import zipfile
from urllib.error import HTTPError
from urllib.request import urlopen

from fastapi.testclient import TestClient
import pytest

from client.cli import Client
from client.transport import HTTPTransport, TransportError
from runtime import updater
from server.app import create_app
from tools.install_client import migrate
from tools.release_client import SOURCE, build


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@contextmanager
def publication_server(directory, handler=QuietHandler):
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(handler, directory=str(directory)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield 'http://127.0.0.1:%s' % server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.fixture
def releases(tmp_path):
    # Only allowlisted sources are staged. Deliberately planted private files
    # must not enter publication, even when the repository contains them.
    source = tmp_path / 'source'
    names = json.loads((SOURCE / 'release/client-files.json').read_text())
    for name in [*names, 'release/client-files.json', 'tools/__init__.py', 'tools/install_client.py']:
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SOURCE / name, target)
    (source / 'data').mkdir()
    (source / 'data/game.sqlite3').write_bytes(b'private database')
    (source / 'token.dpapi').write_bytes(b'private token')
    (source / 'client/assets').mkdir()
    (source / 'client/assets/retired.txt').write_text('retired after v1')
    names.append('client/assets/retired.txt')
    (source / 'release/client-files.json').write_text(json.dumps(names))
    publication = tmp_path / 'publication'
    first = build(source, publication, 1)
    legacy = tmp_path / 'legacy'
    (legacy / 'data').mkdir(parents=True)
    (legacy / 'remote-launcher.json').write_text(json.dumps({'Url': 'https://example.test'}))
    config = b'{"last":{"mode":"remote","url":"https://example.test","run_id":"saved-run"}}'
    pending = b'{"scope":{"mode":"remote","url":"https://example.test"},"command_id":"original-id"}'
    (legacy / 'data/remote-client.json').write_bytes(config)
    (legacy / 'data/remote-client.pending.json').write_bytes(pending)
    (legacy / 'Play.cmd').write_text('legacy launcher stays intact')
    player = tmp_path / 'player'
    migrate(player, publication, legacy=legacy)
    # A real second payload includes both code and resource changes, and removes
    # the retired asset. Tests assert the actual selected files and executed code.
    names.remove('client/assets/retired.txt')
    names.append('client/assets/new.txt')
    (source / 'client/assets/new.txt').write_text('new resource')
    (source / 'client/__init__.py').write_text("RELEASE_MARKER = 'version two'\n")
    (source / 'runtime/launcher.py').write_text((source / 'runtime/launcher.py').read_text('utf-8') + '\n# updated launcher\n', encoding='utf-8')
    (source / 'release/client-files.json').write_text(json.dumps(names))
    second = build(source, publication, 2)
    return source, publication, player, legacy, first, second


def test_real_two_payload_update_no_change_and_state_preserved(releases):
    source, pub, player, legacy, first, second = releases
    before = {p.name: p.read_bytes() for p in (player / 'data').iterdir()}
    old_path, _ = updater.current(player)
    with publication_server(pub) as url, updater.installation_lock(player):
        path, manifest = updater.update(player, url)
        assert manifest == second and path != old_path
        assert (path / 'client/assets/new.txt').read_text() == 'new resource'
        assert not (path / 'client/assets/retired.txt').exists()
        assert (old_path / 'client/assets/retired.txt').exists()
        assert (path / 'runtime/launcher.py').read_bytes() != (old_path / 'runtime/launcher.py').read_bytes()
        result = subprocess.run([sys.executable, '-I', '-c',
            'import sys;sys.path.insert(0,sys.argv[1]);import client;print(client.RELEASE_MARKER)', str(path)],
            capture_output=True, text=True, check=True)
        assert result.stdout.strip() == 'version two'
        pointer = (player / 'current.json').stat().st_mtime_ns
        assert updater.update(player, url) == (path, second)
        assert (player / 'current.json').stat().st_mtime_ns == pointer
    assert {p.name: p.read_bytes() for p in (player / 'data').iterdir()} == before
    assert (legacy / 'Play.cmd').read_text() == 'legacy launcher stays intact'
    assert not list(pub.rglob('*.sqlite3')) and not list(pub.rglob('*.dpapi'))


@pytest.mark.parametrize('failure', ['disconnect', 'corrupt', 'missing', 'syntax', 'commit'])
def test_failed_update_keeps_old_usable_payload(releases, monkeypatch, failure):
    source, pub, player, _, first, second = releases
    old_path, _ = updater.current(player)
    original = (player / 'current.json').read_bytes()
    new_path = pub / 'releases' / updater.digest(updater.canonical(second))
    if failure == 'corrupt':
        (new_path / 'client/cli.py').write_bytes(b'corrupt')
    if failure == 'missing':
        (new_path / 'client/cli.py').unlink()
    if failure == 'syntax':
        (source / 'client/cli.py').write_text('this is invalid python !')
        second = build(source, pub, 3)
    if failure == 'commit':
        replace = updater.os.replace
        def fail_pointer(src, dst):
            if Path(dst).name == 'current.json':
                raise PermissionError('simulated pointer installation failure')
            return replace(src, dst)
        monkeypatch.setattr(updater.os, 'replace', fail_pointer)
    class Interrupted(QuietHandler):
        def do_GET(self):
            if self.path.endswith('/client/cli.py'):
                self.send_response(200)
                self.send_header('Content-Length', '100000')
                self.end_headers()
                self.wfile.write(b'incomplete')
                self.wfile.flush()
                self.close_connection = True
                return
            super().do_GET()
    messages = []
    with publication_server(pub, Interrupted if failure == 'disconnect' else QuietHandler) as url:
        with updater.installation_lock(player):
            path, manifest = updater.update(player, url, output=messages.append)
    assert path == old_path and manifest == first
    assert (player / 'current.json').read_bytes() == original
    assert any('自动更新失败' in text for text in messages)
    assert not list((player / 'versions').glob('.staging-*'))
    # Execute the previous client's actual parser, not merely its version number.
    subprocess.run([sys.executable, '-I', '-c',
        "import sys;sys.path.insert(0,sys.argv[1]);from client.parser import parse_line;assert parse_line('quit')", str(path)], check=True)


@pytest.mark.parametrize('name', ['../data/token.dpapi', '/client/cli.py', 'client/../cli.py',
    'client\\cli.py', 'C:/client/cli.py', 'client/assets/CON.txt', 'client/assets/x.',
    'client/assets/a//b.txt', 'client/assets/a:stream.txt', 'data/remote-client.json',
    'server/app.py', 'client/assets/%2e%2e.txt'])
def test_invalid_paths_never_touch_user_files(releases, name):
    _, pub, player, _, _, second = releases
    second['files'][name] = {'size': 0, 'sha256': updater.digest(b'')}
    channel = {'schema': 1, 'minimum': 0, 'manifest': second}
    (pub / 'channel.json').write_bytes(updater.canonical(channel))
    pointer = (player / 'current.json').read_bytes()
    before = (player / 'data/remote-client.json').read_bytes()
    with publication_server(pub) as url:
        updater.update(player, url, output=lambda _: None)
    assert (player / 'current.json').read_bytes() == pointer
    assert (player / 'data/remote-client.json').read_bytes() == before


def test_network_failure_minimum_requirement_and_rollback(releases):
    _, pub, player, _, first, second = releases
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    assert updater.update(player, f'http://127.0.0.1:{port}', output=lambda _: None)[1] == first
    channel = json.loads((pub / 'channel.json').read_text())
    channel['minimum'] = 2
    (pub / 'channel.json').write_bytes(updater.canonical(channel))
    (pub / 'releases' / updater.digest(updater.canonical(second)) / 'client/cli.py').unlink()
    with publication_server(pub) as url, pytest.raises(updater.UpdateError, match='必须升级'):
        updater.update(player, url, output=lambda _: None)
    assert updater.current(player)[1] == first


def test_process_lock_covers_playing_and_recovers_after_crash(releases):
    _, pub, player, _, _, _ = releases
    script = "from pathlib import Path;from runtime.updater import installation_lock;import sys,time;\nwith installation_lock(Path(sys.argv[1])): print('locked',flush=True);time.sleep(30)"
    process = subprocess.Popen([sys.executable, '-c', script, str(player)],
                               cwd=SOURCE, stdout=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == 'locked'
        with pytest.raises(updater.UpdateError, match='正在运行'):
            with updater.installation_lock(player):
                pytest.fail('Second launcher must not acquire lock')
    finally:
        process.terminate()
        process.wait(timeout=5)
        process.stdout.close()
    with updater.installation_lock(player):
        pass


def test_migration_and_development_guards(releases, tmp_path):
    _, pub, player, legacy, _, _ = releases
    settings = json.loads((player / 'installation.json').read_text())
    assert settings['url'] == 'https://example.test' and settings['run_id'] == 'saved-run'
    assert 'token' not in (player / 'installation.json').read_text().lower()
    assert 'bootstrap.py' in (player / 'Play.cmd').read_text()
    with pytest.raises(updater.UpdateError, match='必须不存在'):
        migrate(player, pub, legacy=legacy)
    with pytest.raises(updater.UpdateError, match='不一致'):
        migrate(tmp_path / 'mismatch', pub, legacy=legacy, url='https://wrong.test')
    (tmp_path / 'dev/.git').mkdir(parents=True)
    with pytest.raises(updater.UpdateError, match='开发仓库'):
        migrate(tmp_path / 'dev/player', pub, legacy=legacy)
    with pytest.raises(updater.UpdateError, match='开发仓库'):
        updater.install(SOURCE, {}, lambda n,s: b'')


def test_portable_bundle_first_install_and_real_bootstrap(releases, tmp_path):
    _, pub, _, legacy, _, second = releases
    extracted = tmp_path / 'extracted'
    with zipfile.ZipFile(pub / 'installer.zip') as archive:
        names = archive.namelist()
        assert not any('game.sqlite3' in n or n.endswith('.dpapi') for n in names)
        assert 'tools/install_client.py' in names and 'Install-Client.cmd' in names
        archive.extractall(extracted)
    target = tmp_path / 'bundle-player'
    with publication_server(pub) as url:
        # Run the actual distributed installer without site-packages.
        result = subprocess.run([sys.executable, '-S', '-m', 'tools.install_client',
            '--publication', str(extracted), '--legacy', str(legacy), '--destination', str(target),
            '--updates', url], cwd=extracted, capture_output=True, text=True, encoding='utf-8')
        assert result.returncode == 0, result.stdout + result.stderr
        result = subprocess.run([sys.executable, str(target / 'bootstrap.py'), '--check'],
            cwd=tmp_path, capture_output=True, text=True, encoding='utf-8',
            env=dict(os.environ, PYTHONUTF8='1'))
        assert result.returncode == 0, result.stdout + result.stderr
        assert updater.current(target)[1] == second


def test_https_only_redirect_and_case_collision(releases):
    _, pub, player, _, _, second = releases
    for url in ('http://example.test', 'https://user:secret@example.test', 'https://example.test?q=token'):
        with pytest.raises(updater.UpdateError):
            updater.release_url(url)
    class Redirect(QuietHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header('Location', 'http://127.0.0.1:1/stolen')
            self.end_headers()
    with publication_server(pub, Redirect) as url, pytest.raises(HTTPError) as exc:
        updater.fetch(url + '/channel.json', 1024)
    assert exc.value.code == 302
    second['files']['client/assets/NEW.txt'] = second['files']['client/assets/new.txt']
    with pytest.raises(updater.UpdateError, match='重复'):
        updater.validate_manifest(second)


@pytest.mark.skipif(os.name != 'nt', reason='Windows DPAPI integration')
def test_windows_dpapi_cache_with_foreign_module_path(tmp_path):
    # Use a disposable synthetic credential, never the user's real token cache.
    helper = SOURCE / 'runtime/Remote-Token.ps1'
    credential = tmp_path / 'synthetic.dpapi'
    script = r'''
param($Helper, $CredentialPath)
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1')
. $Helper
$value = ConvertTo-SecureString -String 'synthetic-test-only' -AsPlainText -Force
[IO.File]::WriteAllText($CredentialPath, (ConvertFrom-SecureString $value))
$value.Dispose()
$original = [IO.File]::ReadAllText($CredentialPath)
$cached = Get-CmdRpgToken -Path $CredentialPath
try {
    $c = New-Object System.Net.NetworkCredential('', $cached)
    if ($c.Password -ne 'synthetic-test-only') { throw 'Cache mismatch' }
    if ([IO.File]::ReadAllText($CredentialPath) -ne $original) { throw 'Cache was replaced' }
    Write-Output 'DPAPI_OK'
} finally { $cached.Dispose() }
'''
    path = tmp_path / 'check-dpapi.ps1'
    path.write_text(script, encoding='utf-8')
    result = subprocess.run(['powershell.exe', '-NonInteractive', '-NoProfile', '-File', str(path),
        str(helper), str(credential)], env=dict(os.environ, PSModulePath=str(tmp_path / 'foreign-modules')),
        capture_output=True, text=True, timeout=15)
    assert result.returncode == 0 and 'DPAPI_OK' in result.stdout


def test_server_explicit_artifacts_and_minimum_without_game_writes(releases, tmp_path):
    _, pub, _, _, _, second = releases
    database = tmp_path / 'server.sqlite3'
    app = create_app(database, 'test-secret', release_directory=pub, minimum_client=2)
    with TestClient(app) as api:
        assert api.get('/health').json() == {'status': 'ok'}
        channel = api.get('/client-updates/channel.json')
        assert channel.status_code == 200 and channel.headers['cache-control'] == 'no-store'
        release = updater.digest(updater.canonical(second))
        assert api.get(f'/client-updates/releases/{release}/client/cli.py').status_code == 200
        for name in ('data/game.sqlite3', 'manifest.json', 'token.dpapi', 'server/app.py'):
            assert api.get(f'/client-updates/releases/{release}/{name}').status_code == 404
        assert api.get('/runs/absent/view').status_code == 401
        response = api.get('/runs/absent/view', headers={'Authorization': 'Bearer test-secret'})
        assert response.status_code == 426
        assert response.json()['detail']['code'] == 'client_upgrade_required'
        assert not database.exists()


def test_end_to_end_pending_receipt_across_real_update(releases, tmp_path):
    source, pub, player, _, _, second = releases
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    token = 'local-test-only'
    database = tmp_path / 'isolated-game.sqlite3'
    environment = dict(os.environ, CMDRPG_DB=str(database), CMDRPG_TOKEN=token,
                       CMDRPG_CLIENT_RELEASES=str(pub), CMDRPG_MIN_CLIENT_VERSION='2')
    process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'server.app:app', '--host', '127.0.0.1',
        '--port', str(port), '--no-access-log', '--log-level', 'error'], cwd=SOURCE, env=environment,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                with urlopen(url + '/health', timeout=.2):
                    break
            except OSError:
                if process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError('Isolated API did not start')
                time.sleep(.05)
        # Old clients are denied at game API even if channel fetch fails.
        with pytest.raises(TransportError) as denied:
            HTTPTransport(url, token).get_view('not-a-run')
        assert denied.value.code == 'client_upgrade_required'
        journal_path = tmp_path / 'old-client.pending.json'
        journal_path.write_text(json.dumps({'scope': {'mode': 'remote', 'url': url},
            'run_id': None, 'operation': 'create',
            'payload': {'command_id': 'old-unconfirmed', 'seed': 42}}), encoding='utf-8')
        original_journal = journal_path.read_bytes()
        old_client = Client(HTTPTransport(url, token), journal_path=journal_path,
                            scope={'mode': 'remote', 'url': url}, output=lambda _: None)
        old_client.load_pending()
        with pytest.raises(TransportError) as upgrade:
            old_client._send_pending()
        assert upgrade.value.code == 'client_upgrade_required'
        assert journal_path.read_bytes() == original_journal
        with updater.installation_lock(player):
            path, _ = updater.update(player, url + '/client-updates')
        assert path.name == updater.digest(updater.canonical(second))
        env = dict(os.environ, CMDRPG_TOKEN=token, CMDRPG_CLIENT_VERSION='2', PYTHONUTF8='1')
        config = player / 'data/remote-client.json'
        pending = player / 'data/remote-client.pending.json'
        pending.unlink()
        config.unlink()
        # Acknowledge a create in an independent process, then reconstruct the
        # exact lost receipt journal. Upgraded client must replay, not create anew.
        transport = HTTPTransport(url, token)
        old = os.environ.get('CMDRPG_CLIENT_VERSION')
        os.environ['CMDRPG_CLIENT_VERSION'] = '2'
        try:
            receipt = transport.create_run({'command_id': 'lost-create', 'seed': 42})
        finally:
            if old is None:
                del os.environ['CMDRPG_CLIENT_VERSION']
            else:
                os.environ['CMDRPG_CLIENT_VERSION'] = old
        pending.write_text(json.dumps({'scope': {'mode': 'remote', 'url': url}, 'run_id': None,
            'operation': 'create', 'payload': {'command_id': 'lost-create', 'seed': 42}}), encoding='utf-8')
        journal = pending.read_bytes()
        # Upgrade again while the real pending request exists.
        build(source, pub, 3)
        with updater.installation_lock(player):
            path, _ = updater.update(player, url + '/client-updates')
        assert pending.read_bytes() == journal
        env['CMDRPG_CLIENT_VERSION'] = '3'
        result = subprocess.run([sys.executable, '-m', 'client', '--url', url, '--config', str(config)],
            input='map\nparty\nq\n', text=True, encoding='utf-8', capture_output=True,
            env=env, cwd=path, timeout=20)
        assert result.returncode == 0, result.stdout + result.stderr
        assert not pending.exists()
        saved = json.loads(config.read_text('utf-8'))
        assert saved['last']['run_id'] == receipt['run_id']
        # One more independent process resumes exactly the same run.
        resumed = subprocess.run([sys.executable, '-m', 'client', '--config', str(config)], input='q\n',
            text=True, encoding='utf-8', capture_output=True, env=env, cwd=path, timeout=20)
        assert resumed.returncode == 0, resumed.stdout + resumed.stderr
        assert json.loads(config.read_text('utf-8')) == saved
    finally:
        process.terminate()
        process.communicate(timeout=5)
