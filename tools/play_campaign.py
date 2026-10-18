"""Play every battle through legal previews; never changes player databases."""

import argparse
from copy import deepcopy
import json

from core.engine import Engine
from .play_slice import choose


def play(templates=None, seed=42, route='road', max_commands=1200, on_step=None):
    engine = Engine()
    state = engine.new_run(templates=templates, seed=seed, campaign=True)
    battles = []
    count = 0

    def write(operation, payload):
        nonlocal state, count
        old = deepcopy(state)
        state, events = engine.react(state, payload) if operation == 'reaction' else engine.execute(state, payload)
        count += 1
        if on_step:
            on_step(old, operation, payload, state, events)

    while count < max_commands:
        if state['phase'] == 'combat':
            write(*choose(engine, state))
            continue
        progress = state['campaign']
        battles.append(dict(node=progress['node'], rounds=state['round'], outcome=state['outcome'],
                            levels=[a['level'] for a in state['actors'].values() if a['side'] == 'party']))
        if state['phase'] != 'victory' or progress['completed']:
            return state, dict(seed=seed, route=route, templates=templates or ['fighter','monk','wizard','cleric'],
                               commands=count, completed=progress['completed'], battles=battles)
        write('command', {'type': 'camp'})
        routes = engine.view(state)['campaign']['routes']
        destination = route if len(routes) > 1 else routes[0]['id']
        write('command', {'type': 'travel', 'destination': destination})
    raise RuntimeError('Campaign smoke player exceeded command limit')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--route', choices=['road', 'ridge'], default='road')
    parser.add_argument('--classes', default='fighter,monk,wizard,cleric')
    args = parser.parse_args()
    _, report = play(args.classes.split(','), args.seed, args.route)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report['completed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
