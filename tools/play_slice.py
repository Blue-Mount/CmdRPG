"""A deterministic smoke player for the slice, not an in-game autopilot.

Uses only Engine.view and legal previews to decide. Used to prove the complete
loop can end with physical, caster, and balanced parties. Human timing and
tactical quality still require manual playtesting.
"""

import argparse
import json
from copy import deepcopy

from core.engine import Engine, GameError
from core import spatial


def legal(engine, state, command):
    try:
        return engine.preview(state, command)
    except GameError:
        return None


def move_for_attack(engine, state, view, actor, enemies, attack_range):
    board = view['map']
    blocked = {a['position'] for a in view['party'] + view['enemies'] if a['id'] != actor['id'] and 'dead' not in a['conditions']}
    choices = []
    goals = enemies or [{'position':'M10','id':'explore'}]
    for target in goals:
        for y in range(board['height']):
            for x in range(board['width']):
                cell = spatial.label(x, y)
                if cell in blocked or cell in board['hazards']:
                    continue
                if spatial.distance(board, cell, target['position']) > attack_range or not spatial.line_of_sight(board, cell, target['position']):
                    continue
                route = spatial.path(board, actor['position'], cell, blocked)
                if route:
                    choices.append((route[1], target['id'], cell, route[0]))
    if not choices:
        return None
    route = min(choices)[3]
    budget, destination = actor['budgets']['movement'], actor['position']
    for source, cell in zip(route, route[1:]):
        price = dict(spatial.neighbors(board, source))[cell]
        if budget < price or cell in board['hazards']:
            break
        budget -= price
        destination = cell
    if destination == actor['position']:
        return None
    command = {'type':'move','actor':actor['id'],'destination':destination}
    return command if legal(engine, state, command) else None


def choose(engine, state):
    view = engine.view(state)
    if view['pending']:
        return ('reaction', 'accept')
    actor = next(a for a in view['party'] if a['id'] == view['active'][0])
    aid = actor['id']
    enemies = sorted([a for a in view['enemies'] if a['hp'] > 0], key=lambda a: (a['hp'], a['id']))
    abilities = {a['id']: a for a in actor['abilities']}
    commands = []
    if 'healing_word' in abilities:
        wounded = sorted([a for a in view['party'] if 'dead' not in a['conditions'] and a['hp'] < a['max_hp'] / 2], key=lambda a: (a['hp'], a['id']))
        commands += [{'type':'cast','actor':aid,'ability':'healing_word','target':a['id']} for a in wounded]
    if actor['hp'] < actor['max_hp'] * .75:
        if 'second_wind' in abilities:
            commands.append({'type':'ability','actor':aid,'ability':'second_wind'})
        commands.append({'type':'potion','actor':aid})
    if 'action_surge' in abilities and actor['budgets']['action']==0:
        commands.append({'type':'ability','actor':aid,'ability':'action_surge'})
    for enemy in enemies:
        if 'fireball' in abilities: commands.append({'type':'cast','actor':aid,'ability':'fireball','target':enemy['id']})
        if 'scorching_ray' in abilities: commands.append({'type':'cast','actor':aid,'ability':'scorching_ray','target':enemy['id']})
        if 'flurry' in abilities: commands.append({'type':'ability','actor':aid,'ability':'flurry','target':enemy['id']})
        if 'magic_missile' in abilities:
            commands.append({'type':'cast','actor':aid,'ability':'magic_missile','target':enemy['id']})
        if 'fire_bolt' in abilities:
            commands.append({'type':'cast','actor':aid,'ability':'fire_bolt','target':enemy['id']})
        if 'sacred_flame' in abilities:
            commands.append({'type':'cast','actor':aid,'ability':'sacred_flame','target':enemy['id']})
        commands.append({'type':'attack','actor':aid,'target':enemy['id']})
        if 'unarmed_strike' in abilities:
            commands.append({'type':'ability','actor':aid,'ability':'unarmed_strike','target':enemy['id']})
    for command in commands:
        if legal(engine, state, command):
            return ('command', command)
    if actor['budgets']['action'] > 0:
        attack_range = 18 if {'fire_bolt','sacred_flame'} & abilities.keys() else 1.5
        command = move_for_attack(engine, state, view, actor, enemies, attack_range)
        if command:
            return ('command', command)
        # A blocked corridor can be cleared by the front party member first.
    return ('command', {'type':'end','actor':aid})


def play(templates=None, seed=42, max_commands=600, on_step=None, level=1):
    engine = Engine()
    state = engine.new_run(templates=templates, seed=seed, levels=[level]*4)
    counts = {'commands':0,'reactions':0,'downed':0}
    for _ in range(max_commands):
        view = engine.view(state)
        if view['phase'] != 'combat':
            return state, {**counts,'outcome':view['outcome'],'level':level,'rounds':view['round'],'seed':seed,'templates':templates or ['fighter','monk','wizard','cleric']}
        operation, payload = choose(engine, state)
        previous = deepcopy(state)
        if operation == 'reaction':
            state, events = engine.react(state, payload)
            counts['reactions'] += 1
        else:
            state, events = engine.execute(state, payload)
        counts['commands'] += 1
        counts['downed'] += sum(e['kind'] == 'downed' for e in events)
        if on_step:
            on_step(previous, operation, payload, state, events)
    raise RuntimeError(f"Smoke player exceeded {max_commands} commands at round {state['round']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--level',type=int,choices=range(1,6),default=1)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--classes', default='fighter,monk,wizard,cleric')
    args = parser.parse_args()
    _, summary = play(args.classes.split(','), args.seed, level=args.level)
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary['outcome'] == 'victory' else 1


if __name__ == '__main__':
    raise SystemExit(main())
