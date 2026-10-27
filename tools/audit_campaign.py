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
}
POLICIES = {'offense': choose, 'support': choose_support}


def audit(seeds=(7, 42), routes=('road', 'ridge'), parties=('balanced', 'physical', 'casters'),
          policies=('offense',), on_run=None):
    runs = []
    for party in parties:
        for route in routes:
            for seed in seeds:
                for policy in policies:
                    try:
                        _, result = play(PARTIES[party], seed=seed, route=route, chooser=POLICIES[policy])
                    except (GameError, RuntimeError) as exc:
                        result = dict(seed=seed, route=route, completed=False, error=str(exc))
                    row = dict(party=party, policy=policy, **result)
                    runs.append(row)
                    if on_run:
                        on_run(row)
    return dict(
        schema_version=2,
        policy='offense: direct attacks; support: round-one bless and stun un-stunned targets with HP >= 20; camp after every victory; no shopping or equipment changes',
        scope='Legal-command smoke runs; observed events only; not human balance or enjoyment validation',
        total=len(runs), completed=sum(r['completed'] for r in runs), runs=runs,
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
              'completed' if row['completed'] else 'FAILED'), file=sys.stderr, flush=True)

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
