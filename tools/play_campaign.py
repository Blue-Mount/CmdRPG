"""Play every battle through legal previews; never changes player databases."""

import argparse
from copy import deepcopy
import json

from core.engine import Engine
from .play_slice import choose


def play(templates=None, seed=42, route='road', max_commands=2400, on_step=None, *, chooser=None, branch=None, shopping=False, reward_loadouts=False):
    engine = Engine()
    state = engine.new_run(templates=templates, seed=seed, campaign=True)
    battles = []
    activity = {}
    count = 0

    def write(operation, payload):
        nonlocal state, count
        old = deepcopy(state)
        state, events = engine.react(state, payload) if operation == 'reaction' else engine.execute(state, payload)
        count += 1
        if old['phase'] == 'combat' and isinstance(payload, dict):
            row = activity.setdefault(old['campaign']['node'], dict(moves=0, movement=0, ends=0, unused_action_ends=0, abilities={}))
            if payload.get('ability'):
                skill = payload['ability']
                row['abilities'][skill] = row['abilities'].get(skill, 0) + 1
            if payload.get('type') == 'move':
                row['moves'] += 1
                row['movement'] += sum(e['details'].get('cost', 0) for e in events
                                       if e['kind'] == 'move' and e['text'].startswith(payload['actor'] + ' '))
            elif payload.get('type') == 'end':
                row['ends'] += 1
                row['unused_action_ends'] += int(old['actors'][payload['actor']]['budgets']['action'] > 0)
        if on_step:
            on_step(old, operation, payload, state, events)

    while count < max_commands:
        if state['phase'] == 'combat':
            write(*(chooser or choose)(engine, state))
            continue
        progress = state['campaign']
        battles.append(dict(node=progress['node'], rounds=state['round'], outcome=state['outcome'],
                            activity=deepcopy(activity.get(progress['node'], {})),
                            report=deepcopy(state.get('battle_report')),
                            levels=[a['level'] for a in state['actors'].values() if a['side'] == 'party']))
        if state['phase'] != 'victory' or progress['completed']:
            return state, dict(seed=seed, route=route, branch=branch, shopping=shopping, reward_loadouts=reward_loadouts, templates=templates or ['fighter','monk','wizard','cleric'],
                               commands=count, completed=progress['completed'], battles=battles)
        if shopping:
            # An explicit equipment policy, using only public inventory/stock.
            # The first rogue uses a rapier so the others can exploit its engagement.
            view=engine.view(state); stock={item['id']:item for item in view['campaign']['shop']}
            first_rogue=next((a['id'] for a in view['party'] if a['template']=='rogue'), None)
            plans={'fighter':['longsword_plus1','gatewarden_blade'], 'monk':['striking_staff','stormstride_staff'],
                   'wizard':['focus_staff','runic_bulwark_staff'], 'cleric':['mercy_mace','dawnwatch_mace'],
                   'warlock':['focus_staff','warlock_trophy']}
            for member in view['party']:
                aid=member['id']
                plan=plans.get(member['template'], ['rapier','duelist_rapier'] if aid==first_rogue else ['scout_bow','rogue_trophy'])
                current=state['actors'][aid]
                item=next((item for item in reversed(plan) if item in current['inventory'] or
                           (item in stock and state['campaign']['gold']>=stock[item]['price'])), None)
                if item and item not in current['inventory']:
                    write('command',dict(type='buy',actor=aid,item=item))
                if item and state['actors'][aid]['equipment']['main_hand']!=item:
                    write('command',dict(type='equip',actor=aid,slot='main_hand',item=item))
        if reward_loadouts:
            for reward in engine.view(state)['settlement'].get('equipment_rewards',[]):
                aid,item=reward['actor'],reward['item']
                data=engine.content['items'][item]
                if data.get('two_handed') and state['actors'][aid]['equipment'].get('off_hand'):
                    write('command',dict(type='equip',actor=aid,slot='off_hand',item=None))
                if state['actors'][aid]['equipment'].get(data['slot'])!=item:
                    write('command',dict(type='equip',actor=aid,slot=data['slot'],item=item))
        routes = engine.view(state)['campaign']['routes']
        preferred = branch if progress['node'] == 'gate' and branch else route
        destination = preferred if any(r['id'] == preferred for r in routes) else routes[0 if route == 'road' else -1]['id']
        write('command', {'type': 'travel', 'destination': destination})
    raise RuntimeError('Campaign smoke player exceeded command limit at %s, round %s' %
                       (state['campaign']['node'], state['round']))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--route', choices=['road', 'ridge'], default='road')
    parser.add_argument('--branch', choices=['switchyard', 'quarry'], help='Second branch; defaults to route-linked choice')
    parser.add_argument('--classes', default='fighter,monk,wizard,cleric')
    parser.add_argument('--shopping', action='store_true', help='Use an explicit legal equipment plan between fights')
    parser.add_argument('--reward-loadouts', action='store_true', help='Legally equip each battle reward before the next encounter')
    args = parser.parse_args()
    _, report = play(args.classes.split(','), args.seed, args.route, branch=args.branch, shopping=args.shopping,reward_loadouts=args.reward_loadouts)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report['completed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
