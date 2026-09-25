"""Songsterr tied-harmonic interpretation, with located retained evidence.

The initial attack owns the playable target. Changing metadata on a tie does
not establish another picked attack or a physically specified pitch gesture.
"""
from copy import deepcopy

FIELDS = frozenset({'hm', 'hp', 'hn', 'hps', 'harmonic_target', 'harmonic_alias'})


def snapshot(effects):
    return {k: deepcopy(v) for k, v in effects.items() if k in FIELDS}


def pitch(target, fret):
    if 'hps' in target:
        return target['hps']
    if 'harmonic_target' in target:
        return fret + target['harmonic_target']['interval']
    return None if target.get('hp') or target.get('hm') else fret


def retain(output, effects, note, track, occurrence, position, end, at, records, state):
    changes = output.get('harmonic_changes', {}).get('events', [])
    authored = snapshot(effects)
    used = {'harmonic_target': deepcopy(changes[-1]['target'])} if changes else snapshot(output)
    if not authored or authored == used:
        return
    _, pi, bi, vi, beat, ni = note.source_id.split(':')
    location = f'parts/{pi}/measures/{bi}/voices/{vi}/beats/{beat}/notes/{ni}'
    feedback = any(t.get('harmonic_target', {}).get('kind') == 'feedback' for t in (authored, used))
    same = pitch(authored, note.fret) is not None and pitch(authored, note.fret) == pitch(used, note.fret)
    target = authored.get('harmonic_target', {})
    timed = (not used and not state.get('harmonic_conflict') and not output.get('mt')
             and target.get('kind') == 'artificial' and at(position) > output['t']
             and not note.slide and not note.slide_in)
    if timed:
        output['harmonic_changes'] = {'version':1, 'events':[{
            'start':at(position)-output['t'], 'end':at(end)-output['t'],
            'target':deepcopy(target), 'source_id':note.source_id}]}
        used = deepcopy(authored)
    else:
        state['harmonic_conflict'] = True
    records.append({'trackId': track.id, 'sourceId': note.source_id, 'location': location,
                    'occurrence': occurrence + 1, 'attack': output['t'],
                    'start': at(position), 'end': at(end), 'string': note.string, 'fret': note.fret,
                    'authored': authored, 'used': used,
                    'rule': 'timed-artificial-contact' if timed else 'feedback-optional' if feedback else 'same-pitch' if same else 'initial-target-continued'})


def finish(output, records, track_id):
    changes = output.get('harmonic_changes', {}).get('events', [])
    if not changes: return
    event = changes[0]
    # V1 does not infer a right-hand contact position during a pitched slide or
    # an unpitched approach/departure. Keep the original attack and evidence.
    if (event['start'] >= output['sus'] or any(k in output for k in
            ('sl','slu','slide_out','slide_out_marks','slide_in_marks'))):
        output.pop('harmonic_changes')
        for row in records:
            if row['trackId'] == track_id and row['attack'] == output['t'] and row['string'] == output['s'] and row['fret'] == output['f']:
                row['used'] = snapshot(output)
                row['rule'] = 'initial-target-continued'
    else:
        event['end'] = output['sus']


def report_findings(performance, report):
    from .compatibility import add_finding
    for row in performance.get('harmonicTieEvidence', []):
        add_finding(report, feature='note.tied_harmonic', category='game_limitation', impact='display_or_expression',
                    message=('An artificial-harmonic contact starts during this sustain. Its timing and target are preserved; the updated game displays the contact without another picked attack.'
                             if row['rule'] == 'timed-artificial-contact' else
                             'A harmonic change during a tied sustain is retained in the source. Gameplay continues the established target without an additional picked attack.'),
                    location=row['location'] + f"@visit{row['occurrence']}",
                    value={k: row[k] for k in ('authored', 'used', 'rule')},
                    trackId=row['trackId'])


def archive_evidence(performance, source_path):
    import hashlib
    rows = performance.get('harmonicTieEvidence', [])
    return {'version': 2, 'policy': 'songsterr-tied-harmonics-v2',
            'sourceSha256': hashlib.sha256(source_path.read_bytes()).hexdigest(),
            'timeDomain': 'score_seconds', 'continuations': deepcopy(rows)}
