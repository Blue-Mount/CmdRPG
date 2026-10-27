"""Observed combat accounting, independent of the bounded event history."""


def new_report(party):
    return {
        'version': 1, 'rounds': 1,
        'enemy_hp_lost': 0, 'party_hp_lost': 0, 'absorbed': 0,
        'healing': 0, 'downed': 0, 'deaths': 0,
        'support': dict(version=1, bless_hits=0, bless_saves=0, stunned_turns=0, automatic_failures=0),
        'members': {a['id']: dict(damage=0, healing=0, taken=0, absorbed=0,
                                attacks=0, hits=0, criticals=0, downed=0, costs={})
                    for a in party},
    }


def record(state, kind, subject, details):
    # Called only after the engine's visibility filter. Old battles deliberately
    # remain uninstrumented: their truncated logs cannot reconstruct exact totals.
    report = state.get('battle_report')
    if report is None or state['phase'] != 'combat':
        return
    report['rounds'] = state['round']
    members = report['members']
    member = members.get(subject['id']) if subject else None
    source = members.get(details.get('source_actor'))
    support = report.get('support')
    if support is not None and subject:
        if kind == 'attack' and member is not None and details.get('bless_changed'):
            support['bless_hits'] += 1
        elif kind == 'save' and member is not None and details.get('bless_changed'):
            support['bless_saves'] += 1
        elif subject['side'] == 'enemy':
            if kind == 'turn_skip' and 'stunned' in subject['conditions']:
                support['stunned_turns'] += 1
            elif kind == 'save' and details.get('automatic'):
                support['automatic_failures'] += 1
    if kind == 'damage' and subject:
        lost = details['hp_lost']
        if subject['side'] == 'enemy':
            report['enemy_hp_lost'] += lost
            if source is not None:
                source['damage'] += lost
        elif member is not None:
            report['party_hp_lost'] += lost
            report['absorbed'] += details['absorbed']
            member['taken'] += lost
            member['absorbed'] += details['absorbed']
    elif kind == 'heal' and member is not None:
        actual = details['actual_healing']
        report['healing'] += actual
        if source is not None:
            source['healing'] += actual
    elif kind == 'attack' and member is not None:
        member['attacks'] += 1
        member['hits'] += int(details['hit'])
        member['criticals'] += int(details['critical'])
    elif kind == 'resource' and member is not None:
        for key, value in details.get('cost', {}).items():
            if key not in ('action', 'bonus', 'reaction', 'movement'):
                member['costs'][key] = member['costs'].get(key, 0) + value
    elif kind == 'downed' and member is not None:
        report['downed'] += 1
        member['downed'] += 1
    elif kind == 'death' and member is not None:
        report['deaths'] += 1
