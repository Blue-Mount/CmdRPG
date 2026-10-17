"""Real TCP + urllib + process restart, in addition to ASGI unit tests."""

from contextlib import contextmanager
from pathlib import Path
import os
import secrets
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

from client.cli import Client
from client.transport import HTTPTransport
from core.engine import GameError
from tools.play_slice import choose


@contextmanager
def running_server(database, token, port):
    environment = dict(os.environ, CMDRPG_DB=str(database), CMDRPG_TOKEN=token)
    process = subprocess.Popen(
        [sys.executable, '-m', 'uvicorn', 'server.app:app', '--host','127.0.0.1',
         '--port',str(port),'--workers','1','--no-access-log','--log-level','error'],
        cwd=Path(__file__).resolve().parents[1], env=environment,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
    )
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                with urlopen(f'http://127.0.0.1:{port}/health',timeout=.2) as response:
                    assert response.status == 200
                    break
            except OSError:
                if process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError('Test API failed to start')
                time.sleep(.05)
        yield
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        process.communicate()


def test_real_http_client_full_encounter_and_server_restart(tmp_path):
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1',0))
        port = reservation.getsockname()[1]
    token = secrets.token_urlsafe(32)
    database = tmp_path / 'remote.sqlite3'
    transport = HTTPTransport(f'http://127.0.0.1:{port}',token)
    client = Client(transport, output=lambda _:None)
    class VisibleAdapter:
        def view(self, state):
            return state
        def preview(self, state, command):
            try:
                return transport.preview(client.run_id, command)
            except Exception as exc:
                if hasattr(exc,'code'):
                    raise GameError(exc.code, exc.message) from exc
                raise
    adapter = VisibleAdapter()
    total = 0
    run_id = None
    for session in range(2):
        with running_server(database,token,port):
            if session == 0:
                client.create(seed=713,templates=['fighter','fighter','monk','monk'])
                run_id = client.run_id
            else:
                old_view = client.view
                client = Client(transport,run_id=run_id,output=lambda _:None)
                client.refresh()
                assert client.view == old_view
            for _ in range(10 if session == 0 else 100):
                if client.view['phase'] != 'combat':
                    break
                operation, payload = choose(adapter,client.view)
                if operation == 'reaction':
                    client.handle('1' if payload == 'accept' else '2')
                else:
                    # Only Chinese menu numbers and coordinates cross the UI;
                    # target discovery uses the real urllib options endpoint.
                    aid = payload['actor']
                    if client.ui.stage == 'actions' and client.ui.selected != aid:
                        client.handle('0')
                    if client.ui.stage == 'home':
                        client.handle(str(next(i for i, a in enumerate(client.view['party'], 1) if a['id'] == aid)))
                    member = next(a for a in client.view['party'] if a['id'] == aid)
                    number = next(i for i, entry in enumerate(member['menu_actions'], 1)
                                  if entry['command']['type'] == payload['type']
                                  and entry['command'].get('ability') == payload.get('ability'))
                    client.handle(str(number))
                    if payload['type'] == 'move':
                        client.handle(payload['destination'])
                    elif 'target' in payload:
                        client.handle(str(next(i for i, entry in enumerate(client.ui.entries, 1)
                                               if entry['target'] == payload['target'])))
                assert client.ui.stage == 'confirm'
                client.handle('c')
                total += 1
                assert client.pending is None
    assert client.view['phase'] == 'victory'
    assert client.view['revision'] == total
    assert 0 < total < 100  # Tactical reach and map revisions may change command count.
    assert database.is_file()
