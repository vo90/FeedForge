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


def retain(output, effects, note, track, occurrence, position, end, at, records):
    authored, used = snapshot(effects), snapshot(output)
    if not authored or authored == used:
        return
    _, pi, bi, vi, beat, ni = note.source_id.split(':')
    location = f'parts/{pi}/measures/{bi}/voices/{vi}/beats/{beat}/notes/{ni}'
    feedback = any(t.get('harmonic_target', {}).get('kind') == 'feedback' for t in (authored, used))
    same = pitch(authored, note.fret) is not None and pitch(authored, note.fret) == pitch(used, note.fret)
    records.append({'trackId': track.id, 'sourceId': note.source_id, 'location': location,
                    'occurrence': occurrence + 1, 'attack': output['t'],
                    'start': at(position), 'end': at(end), 'string': note.string, 'fret': note.fret,
                    'authored': authored, 'used': used,
                    'rule': 'feedback-optional' if feedback else 'same-pitch' if same else 'initial-target-continued'})


def report_findings(performance, report):
    from .compatibility import add_finding
    for row in performance.get('harmonicTieEvidence', []):
        add_finding(report, feature='note.tied_harmonic', category='game_limitation', impact='display_or_expression',
                    message='A harmonic change during a tied sustain is retained in the source. Gameplay continues the established target without an additional picked attack.',
                    location=row['location'] + f"@visit{row['occurrence']}",
                    value={k: row[k] for k in ('authored', 'used', 'rule')},
                    trackId=row['trackId'])


def archive_evidence(performance, source_path):
    import hashlib
    rows = performance.get('harmonicTieEvidence', [])
    return {'version': 1, 'policy': 'songsterr-tied-harmonics-v1',
            'sourceSha256': hashlib.sha256(source_path.read_bytes()).hexdigest(),
            'timeDomain': 'score_seconds', 'continuations': deepcopy(rows)}
