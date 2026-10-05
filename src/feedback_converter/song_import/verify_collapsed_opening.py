"""Reconstruct skipped-opening omissions independently from retained source."""
from copy import deepcopy
from .verify_voices import source_ids


def project(result, alignment):
    policy = alignment.get('collapsedOpening')
    if not policy:
        return result
    boundary = alignment['anchors'][policy['measureCount']]['score']
    if any(any(type(n["note"].get(k)) is int and 24 < n["note"][k] <= 48 for k in ("f", "sl", "slu"))
           for p in result["parts"] for n in p["notes"]):
        result["pre_opening_parts"] = deepcopy(result["parts"])
    rows = []
    for part in result['parts']:
        if 'sync_notes' in part:
            part['sync_notes'] = [n for n in part['sync_notes'] if n['_scoreStart'] >= boundary - 1e-8]
        kept = []
        previous = {}
        for item in part['notes']:
            start, end = item.pop('_scoreStart'), item.pop('_scoreEnd')
            note = item['note']
            if start < boundary - 1e-8:
                if start < 0 or end > boundary + 1e-8:
                    raise ValueError('Skipped opening crosses a playable gesture')
                rows.append({'trackId': part['source'].id, 'sourceIds': source_ids(item),
                             'string': note['s'], 'fret': note['f'], 'scoreStart': start, 'scoreEnd': end})
            else:
                prior = previous.get(note['s'])
                if prior and prior[1] < boundary - 1e-8 and (prior[0].get('ln') or note.get('ho') or note.get('po')):
                    raise ValueError('Skipped opening has a linked destination')
                kept.append(item)
            previous[note['s']] = note, start
        if part['notes'] and not kept:
            raise ValueError('Skipped opening removes an entire arrangement')
        part['notes'] = kept
        part['notation_measures'] = [m for m in part['notation_measures'] if m['idx'] > policy['measureCount']]
        part['notation_beats'] = [b for b in part['notation_beats'] if b['measure'] > policy['measureCount']]
    for group in result['strums']:
        if len({n['t'] < boundary - 1e-8 for n in group['notes']}) > 1:
            raise ValueError('Skipped opening crosses an authored strum')
    rows.sort(key=lambda n: (n['trackId'], n['scoreStart'], n['string'], n['sourceIds']))
    result['collapsed_opening_notes'] = rows
    return result


def verify(wanted, alignment, recipe, retained, source_hash, check):
    from .verify_policy_receipt import compare
    policy = alignment.get('collapsedOpening')
    if not policy:
        if retained is not None or recipe.get('collapsedOpening') is not None:
            check.fail('collapsed_opening', 'import', 'Unexpected skipped-opening evidence')
        return
    if recipe.get('preservationContract', 0) < 87:
        check.fail('collapsed_opening', 'manifest/song_import', 'Skipped opening requires contract 87')
    check.equal('collapsed_opening', 'manifest/song_import/collapsedOpening', policy, recipe.get('collapsedOpening'))
    check.equal('collapsed_opening', 'manifest/song_import/collapsedOpeningFile',
                'import/collapsed-opening.json', recipe.get('collapsedOpeningFile'))
    rows = wanted['collapsed_opening_notes']
    check.equal('collapsed_opening', 'import/collapsed-opening/noteCount', len(rows), policy.get('noteCount'))
    compare({**policy, 'sourceSha256': source_hash, 'timeDomain': 'score_seconds', 'notes': rows},
            retained, check, 'collapsed_opening')
