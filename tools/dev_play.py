"""Isolated M3 playground; never reads the normal client config or database."""

import argparse
from pathlib import Path
import secrets
import socket
import tempfile
import threading
import time
import uuid

from core.engine import Engine
from server.storage import Repository


class PlaygroundEngine(Engine):
    def __init__(self, levels, loadouts=None):
        super().__init__()
        self.levels, self.loadouts = levels, loadouts

    def new_run(self, name="M3开发试玩", templates=None, seed=None):
        state = super().new_run(
            name, templates, seed, levels=self.levels, loadouts=self.loadouts
        )
        state["development"] = True
        state["battle_snapshot"]["development"] = True
        return state


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--level", type=int, choices=range(1, 6), default=5)
    parser.add_argument("--levels", help="四人独立等级，例如 2,3,4,5")
    parser.add_argument("--classes", default="fighter,monk,wizard,cleric")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--equip",
        action="append",
        default=[],
        help="P1:off_hand:none 或 P1:main_hand:greatsword；依次处理",
    )
    parser.add_argument(
        "--prepare",
        action="append",
        default=[],
        help="P3:magic_missile,fireball；牧师须保留领域恒定法术",
    )
    parser.add_argument(
        "--http", action="store_true", help="启动独立回环服务并使用真实HTTP终端"
    )
    args = parser.parse_args(argv)
    try:
        levels = (
            [int(x) for x in args.levels.split(",")]
            if args.levels
            else [args.level] * 4
        )
        loadouts = []
        for value in args.equip:
            actor, slot, item = value.split(":")
            loadouts.append(
                dict(
                    type="equip",
                    actor=actor.upper(),
                    slot=slot,
                    item=None if item == "none" else item,
                )
            )
        for value in args.prepare:
            actor, spells = value.split(":")
            loadouts.append(
                dict(
                    type="prepare",
                    actor=actor.upper(),
                    spells=spells.split(",") if spells else [],
                )
            )
        engine = PlaygroundEngine(levels, loadouts)
        # Validate before allocating any on-disk save.
        engine.new_run(templates=args.classes.split(","), seed=args.seed)
    except Exception as exc:
        parser.error(str(exc))
    directory = Path(tempfile.mkdtemp(prefix="CmdRPG-M3-"))
    database, config = directory / "play.sqlite3", directory / "client.json"
    store = Repository(database, engine=engine)
    receipt = store.create_run(
        "M3开发试玩", args.classes.split(","), args.seed, str(uuid.uuid4())
    )
    run_id = receipt["run_id"]
    print(
        f'独立开发存档：{database}\n恢复：python -m client --db "{database}" --config "{config}" --run {run_id}',
        flush=True,
    )
    from client.cli import main as client_main

    common = ["--config", str(config), "--run", run_id]
    if not args.http:
        store.close()
        return client_main(["--db", str(database), *common])
    import uvicorn
    from server.app import create_app

    token = secrets.token_urlsafe(32)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(create_app(repository=store, token=token), log_level="error")
    )
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [sock]}, daemon=True
    )
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("开发HTTP服务启动失败")
            time.sleep(0.02)
        return client_main(
            ["--url", f"http://127.0.0.1:{port}", "--token=" + token, *common]
        )
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()


if __name__ == "__main__":
    raise SystemExit(main())
