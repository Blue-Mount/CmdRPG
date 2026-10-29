"""Isolated growth/campaign playground; never reads player config or databases."""

import argparse
from pathlib import Path
import sys
import tempfile
import uuid

from core.engine import Engine
from core.growth import CONTENT_VERSION, RULES_VERSION
from saves.storage import Repository


class PlaygroundEngine(Engine):
    def __init__(self, levels, loadouts=None, battle_slice=False, boss_slice=False, encounter=None):
        super().__init__()
        self.battle_slice = battle_slice
        self.boss_slice = boss_slice
        self.encounter_slice = encounter
        if battle_slice or boss_slice or encounter:
            from core.growth import DATA
            self.content.update(self._campaign_pack(encounter or ('heart' if boss_slice else 'forge')))
            self.content['version'] = CONTENT_VERSION
            self.content['rules_version'] = RULES_VERSION
            self.content['abilities'].update(DATA['abilities'])
            self.content['items'].update(DATA['items'])
            self._install_classes()
        self.levels, self.loadouts = levels, loadouts

    def new_run(self, name="M3开发试玩", templates=None, seed=None, *, campaign=False):
        if campaign:
            return super().new_run(name, templates, seed, campaign=True)
        state = super().new_run(
            name, templates, seed, levels=self.levels, loadouts=self.loadouts
        )
        state["development"] = True
        if self.battle_slice: state['battle_slice'] = True
        if self.boss_slice: state['boss_slice'] = True
        if self.encounter_slice: state['encounter_slice'] = self.encounter_slice
        state["battle_snapshot"]["development"] = True
        if self.battle_slice: state['battle_snapshot']['battle_slice'] = True
        if self.boss_slice: state['battle_snapshot']['boss_slice'] = True
        if self.encounter_slice: state['battle_snapshot']['encounter_slice'] = self.encounter_slice
        return state


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--level", type=int, choices=range(1, 9), default=None)
    parser.add_argument("--levels", help="四人独立等级，例如 2,3,4,5")
    parser.add_argument("--classes", default="fighter,monk,wizard,cleric")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument('--campaign', action='store_true', help='十四站完整战役雏形，从1级开始')
    parser.add_argument('--battle-slice', action='store_true', help='在熔炉地图反复体验四职业爆发、控制与装备；默认5级')
    parser.add_argument('--boss-slice', action='store_true', help='独立两阶段Boss试玩，默认8级；战后11号可重新演练')
    parser.add_argument('--encounter', choices=tuple(Engine().campaign_content['nodes']), help='独立遭遇试玩，默认8级，战后11号可重新演练')
    parser.add_argument('--ui', choices=('auto', 'cards', 'plain'), default='auto', help='终端界面模式')
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
    if sum(bool(x) for x in (args.battle_slice, args.boss_slice, args.encounter)) > 1:
        parser.error('请选择一个独立试玩场景。')
    if args.campaign and (args.level is not None or args.levels or args.equip or args.prepare or args.battle_slice or args.boss_slice or args.encounter):
        parser.error('战役从1级起步，不能指定开发局等级、装备或法术准备。')
    try:
        levels = (
            [int(x) for x in args.levels.split(",")]
            if args.levels
            else [args.level or (8 if args.boss_slice or args.encounter else 5)] * 4
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
        engine = Engine() if args.campaign else PlaygroundEngine(levels, loadouts, args.battle_slice, args.boss_slice, args.encounter)
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
    if args.boss_slice or args.battle_slice or args.encounter:
        print('独立试玩：%s · 队伍等级 %s' % (engine.content['encounter']['name'], '/'.join(str(n) for n in levels)), flush=True)
    print(
        f'独立开发存档：{database}\n恢复：python -m client --db "{database}" --config "{config}" --run {run_id}',
        flush=True,
    )
    from client.cli import main as client_main

    common = ["--config", str(config), "--run", run_id, "--ui", args.ui]
    store.close()
    return client_main(["--db", str(database), *common])


if __name__ == "__main__":
    raise SystemExit(main())
