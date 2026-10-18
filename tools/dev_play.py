"""Isolated growth/campaign playground; never reads player config or databases."""

import argparse
from pathlib import Path
import sys
import tempfile
import uuid

from core.engine import Engine
from saves.storage import Repository


class PlaygroundEngine(Engine):
    def __init__(self, levels, loadouts=None):
        super().__init__()
        self.levels, self.loadouts = levels, loadouts

    def new_run(self, name="M3开发试玩", templates=None, seed=None, *, campaign=False):
        if campaign:
            return super().new_run(name, templates, seed, campaign=True)
        state = super().new_run(
            name, templates, seed, levels=self.levels, loadouts=self.loadouts
        )
        state["development"] = True
        state["battle_snapshot"]["development"] = True
        return state


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--level", type=int, choices=range(1, 6), default=5)
    parser.add_argument("--levels", help="四人独立等级，例如 2,3,4,5")
    parser.add_argument("--classes", default="fighter,monk,wizard,cleric")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument('--campaign', action='store_true', help='五站完整战役雏形，从1级开始')
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
    args = parser.parse_args(argv)
    if args.campaign and (args.levels or args.equip or args.prepare):
        parser.error('战役从1级起步，不能指定开发局等级、装备或法术准备。')
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
        engine = Engine() if args.campaign else PlaygroundEngine(levels, loadouts)
        # Validate before allocating any on-disk save.
        engine.new_run(templates=args.classes.split(","), seed=args.seed, campaign=args.campaign)
    except Exception as exc:
        parser.error(str(exc))
    directory = Path(tempfile.mkdtemp(prefix="CmdRPG-M4-" if args.campaign else 'CmdRPG-M3-'))
    database, config = directory / "play.sqlite3", directory / "client.json"
    store = Repository(database, engine=engine)
    receipt = store.create_run(
        "M4战役试玩" if args.campaign else 'M3开发试玩', args.classes.split(","), args.seed, str(uuid.uuid4()), campaign=args.campaign
    )
    run_id = receipt["run_id"]
    print(
        f'独立开发存档：{database}\n恢复：python -m client --db "{database}" --config "{config}" --run {run_id}',
        flush=True,
    )
    from client.cli import main as client_main

    common = ["--config", str(config), "--run", run_id]
    store.close()
    return client_main(["--db", str(database), *common])


if __name__ == "__main__":
    raise SystemExit(main())
