"""Bounded, public-information comparisons for the opt-in tactical playground."""

import argparse
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path

from core import spatial
from core.playground import GEAR, SCENES
from tools.dev_play import PlaygroundEngine
from tools.play_slice import choose, legal


PARTIES = {'mixed': ['fighter', 'rogue', 'wizard', 'cleric'],
           'classic': ['fighter', 'monk', 'wizard', 'cleric']}
LEVELS = {'foundry': 3, 'archive': 4, 'sanctum': 7}


def objective_move(engine, state, view, actor, destination):
    """Approach a visible public landmark using only observed occupied cells."""
    board = view['map']
    blocked = {a['position'] for a in view['party'] + view['enemies']
               if a['id'] != actor['id'] and 'dead' not in a['conditions']}
    routes = spatial.paths(board, actor['position'], blocked | set(board['hazards']))
    choices = [(cost, cell, route) for cell, (route, cost) in routes.items()
               if spatial.reach_distance(board, cell, destination) <= 1.5
               and spatial.line_of_sight(board, cell, destination)]
    if not choices:
        return None
    _, _, route = min(choices)
    cell = actor['position']
    for step in route[1:]:
        candidate = dict(type='move', actor=actor['id'], destination=step)
        if not legal(engine, state, candidate):
            break
        cell = step
    if cell != actor['position']:
        return dict(type='move', actor=actor['id'], destination=cell)
    return None


def choose_mechanism(engine, state, used):
    """A deliberately simple alternative, not an optimizer or human proxy.

    One designated actor attempts one interaction per battle; rogue withdraws
    toward visible allies after a sneak attack. All other decisions use the
    existing support policy. No enemy/RNG/private state is used for decisions.
    """
    view = engine.view(state)
    if view['pending']:
        return 'reaction', 'accept'
    actor = next(a for a in view['party'] if a['id'] == view['active'][0])
    aid = actor['id']
    if any('撤步已就绪' in note for note in actor.get('tactical_notes', [])):
        allies = [a for a in view['party'] if a['id'] != aid and a['hp'] > 0]
        options = engine.options(state, dict(type='withdraw', actor=aid))['entries']
        candidates = [x for x in options if x['available'] and not x['preview'].get('hazards')]
        if candidates and allies:
            entry = min(candidates, key=lambda x: (min(spatial.distance(view['map'], x['destination'], a['position']) for a in allies), x['destination']))
            return 'command', entry['preview']['command']
    objects = view.get('tactical_objects', {})
    ident = 'sluice' if 'sluice' in objects else 'shelter'
    operator = 'P2' if ident == 'sluice' else 'P1'
    if aid == operator and ident in objects and ident not in used and actor['budgets']['action'] > 0:
        command = dict(type='interact', actor=aid, object=ident)
        if legal(engine, state, command):
            return 'command', command
        command = objective_move(engine, state, view, actor, objects[ident]['position'])
        if command:
            return 'command', command
    return choose(engine, state, support=True, prioritize_support=True)


def play(scene, templates, seed, *, tactical, gear, policy, limit=500, trace=False):
    setup = [dict(type='equip', actor=f'P{i}', slot=GEAR[c][0], item=GEAR[c][1])
             for i, c in enumerate(templates, 1) if gear and c in GEAR]
    engine = PlaygroundEngine([LEVELS[scene]] * 4, setup, encounter=scene, tactical=tactical)
    state = engine.new_run(templates=templates, seed=seed)
    actions, triggers, used, history = Counter(), Counter(), set(), []
    first_contact = None
    movement = 0
    last_enemy_counts = []
    for index in range(limit):
        if state['phase'] != 'combat':
            break
        old = state
        operation, command = choose_mechanism(engine, state, used) if policy == 'mechanism' else choose(engine, state, support=policy == 'support')
        state, events = engine.react(state, command) if operation == 'reaction' else engine.execute(state, command)
        if isinstance(command, dict):
            actions[command.get('ability', command['type'])] += 1
            if command['type'] == 'interact':
                used.add(command['object'])
        else:
            actions['reaction_' + command] += 1
        for event in events:
            if event['kind'] == 'damage' and first_contact is None:
                first_contact = old['round']
            if event['kind'] == 'equipment_trigger':
                triggers[event['details']['trait']] += 1
            if event['kind'] == 'mitigation':
                triggers['observed_damage_prevented'] += event['details']['prevented']
            if event['kind'] == 'move' and event['text'].startswith('P'):
                movement += event['details']['cost']
        remaining = sum(a['hp'] > 0 for a in state['actors'].values() if a['side'] == 'enemy')
        if remaining == 1:
            last_enemy_counts.append(state['round'])
        if trace:
            history.append(dict(revision=state['revision'], round=old['round'], operation=operation,
                                command=deepcopy(command), events=events))
    report = state['battle_report']
    result = dict(scene=scene, level=LEVELS[scene], seed=seed, templates=templates,
                  tactical=tactical, gear=gear, policy=policy, outcome=state['phase'], rounds=state['round'],
                  commands=sum(actions.values()), first_contact_round=first_contact, party_movement=movement,
                  last_enemy_rounds=len(set(last_enemy_counts)), actions=dict(actions), triggers=dict(triggers),
                  interactions=sorted(used), party_hp_lost=report['party_hp_lost'], downed=report['downed'],
                  report=deepcopy(report))
    if trace:
        result['trace'] = history
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seeds', type=int, nargs='+', default=[7, 42])
    parser.add_argument('--scenes', choices=SCENES, nargs='+', default=list(SCENES))
    parser.add_argument('--parties', choices=tuple(PARTIES), nargs='+', default=['mixed'])
    parser.add_argument('--trace', action='store_true', help='保留全部正式命令与可见事件，用于人工复盘')
    parser.add_argument('--output', type=Path, default=Path('.qa/tactical-audit.json'))
    args = parser.parse_args(argv)
    rows = []
    variants = [(False, False, 'offense'), (True, False, 'offense'),
                (False, True, 'offense'), (True, True, 'offense'), (True, True, 'mechanism')]
    for party in args.parties:
        for scene in args.scenes:
            for seed in args.seeds:
                for tactical, gear, policy in variants:
                    row = play(scene, PARTIES[party], seed, tactical=tactical, gear=gear, policy=policy, trace=args.trace)
                    rows.append(dict(party=party, **row))
                    print(f'{party}/{scene}/seed{seed} mechanics={tactical} gear={gear} {policy}: {row["outcome"]}, {row["rounds"]} rounds', flush=True)
    output = dict(scope='Legal-command smoke comparison, not human enjoyment or balance certification.',
                  rules='0.9.0-tactical-lab', completed=sum(r['outcome'] == 'victory' for r in rows), total=len(rows), runs=rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return int(any(r['outcome'] == 'combat' for r in rows))


if __name__ == '__main__':
    raise SystemExit(main())
