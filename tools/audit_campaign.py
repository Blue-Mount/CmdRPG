"""Repeatable in-memory campaign audit; never opens player saves."""

import argparse
import json
from pathlib import Path

from core.engine import GameError
from .play_campaign import play
from .play_slice import choose, choose_support


PARTIES = {
    'balanced': ['fighter', 'monk', 'wizard', 'cleric'],
    'physical': ['fighter', 'fighter', 'monk', 'monk'],
    'casters': ['wizard', 'wizard', 'cleric', 'cleric'],
    'expanded': ['fighter', 'rogue', 'warlock', 'cleric'],
    'new_classes': ['rogue', 'rogue', 'warlock', 'warlock'],
    'warlocks': ['warlock'] * 4,
    'rogues': ['rogue'] * 4,
}
POLICIES = {'offense': choose, 'support': choose_support, 'equipped': choose_support}


def diagnostics(runs):
    """Flag smoke-player stalls without mistaking win rate for enjoyment."""
    from statistics import median
    battles = [b for row in runs for b in row.get('battles', [])]
    suspects = [dict(party=row['party'], route=row['route'], seed=row['seed'], node=b['node'],
                     rounds=b['rounds'], unused_action_ends=b.get('activity', {}).get('unused_action_ends', 0))
                for row in runs for b in row.get('battles', [])
                if b['rounds'] >= 20 or (b['rounds'] >= 10 and b.get('activity', {}).get('unused_action_ends', 0) > 15)]
    by_node = {}
    for battle in battles:
        by_node.setdefault(battle['node'], []).append(battle['rounds'])
    return dict(battles=len(battles), max_rounds=max((b['rounds'] for b in battles), default=0),
                suspect_stalls=suspects,
                downed=sum((b.get('report') or {}).get('downed', 0) for b in battles),
                deaths=sum((b.get('report') or {}).get('deaths', 0) for b in battles),
                nodes={node:dict(samples=len(rounds),minimum=min(rounds),median=median(rounds),maximum=max(rounds))
                       for node,rounds in sorted(by_node.items())})


def audit(seeds=(7, 42), routes=('road', 'ridge'), parties=('balanced', 'physical', 'casters'),
          policies=('offense',), on_run=None):
    runs = []
    for party in parties:
        for route in routes:
            for seed in seeds:
                for policy in policies:
                    try:
                        _, result = play(PARTIES[party], seed=seed, route=route, chooser=POLICIES[policy], shopping=policy=='equipped')
                    except (GameError, RuntimeError) as exc:
                        result = dict(seed=seed, route=route, completed=False, error=str(exc))
                    row = dict(party=party, policy=policy, **result)
                    runs.append(row)
                    if on_run:
                        on_run(row)
    return dict(
        schema_version=2,
        policy='offense: direct attacks; support: round-one bless/stun; equipped: support plus legal weapon purchases/rewards, first rogue uses rapier; victory automatically restores the party; offense/support do not shop',
        scope='Legal-command smoke runs; observed events only; not human balance or enjoyment validation',
        total=len(runs), completed=sum(r['completed'] for r in runs), runs=runs,
        diagnostics=diagnostics(runs),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seeds', nargs='+', type=int, default=[7, 42])
    parser.add_argument('--routes', nargs='+', choices=['road', 'ridge'], default=['road', 'ridge'])
    parser.add_argument('--parties', nargs='+', choices=list(PARTIES), default=list(PARTIES))
    parser.add_argument('--policies', nargs='+', choices=list(POLICIES), default=['offense'])
    parser.add_argument('--output', type=Path, help='Optional JSON report path')
    args = parser.parse_args()
    def progress(row):
        import sys
        print('%s / %s / seed%s / %s: %s' % (row['party'], row['route'], row['seed'], row['policy'],
              'completed' if row['completed'] else 'FAILED: ' + row.get('error', 'defeated at ' + str((row.get('battles') or [{}])[-1].get('node')))), file=sys.stderr, flush=True)

    result = audit(args.seeds, args.routes, args.parties, args.policies, on_run=progress)
    text = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding='utf-8')
        print('%s/%s campaigns completed; report: %s' % (result['completed'], result['total'], args.output))
    else:
        print(text, end='')
    return 0 if result['completed'] == result['total'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
