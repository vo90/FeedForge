"""Account for an undefined slide into an explicitly authored unpitched mute."""
from copy import deepcopy
import hashlib


def origin(note, track, occurrence, attack, start):
    _, pi, mi, vi, bi, ni = note.source_id.split(':')
    return {'trackId': track.id, 'sourceId': note.source_id,
            'location': f'parts/{pi}/measures/{mi}/voices/{vi}/beats/{bi}/notes/{ni}',
            'occurrence': occurrence + 1, 'attack': attack, 'start': start,
            'string': note.string, 'fret': None if note.fret == 127 else note.fret,
            'muted': note.effects.get('mt') is True, 'authored': {'slide': note.slide}}


def retain(start, note, occurrence, time):
    return {**deepcopy(start), 'target': {'sourceId': note.source_id,
            'occurrence': occurrence + 1, 'time': time, 'fret': None, 'muted': True},
            'used': {'rule': 'omit-undefined-slide-keep-authored-mute'}}


def report_findings(performance, report):
    from .compatibility import add_finding
    for row in performance.get('undefinedSlideEvidence', []):
        add_finding(report, feature='note.undefined_slide_to_mute', category='game_limitation',
                    impact='display_or_expression',
                    message='The slide ends on an explicit X without a destination fret or direction. '
                            'The written notes, mute and timing are retained; only the undefined slide path is omitted. '
                            'Its original instruction is saved with the source.',
                    location=row['location'] + f"@visit{row['occurrence']}",
                    value={**{k: row[k] for k in ('authored', 'used')},
                           'target': {k: v for k, v in row['target'].items() if k != 'time'}},
                    trackId=row['trackId'])


def archive_evidence(performance, source_path):
    return {'version': 1, 'policy': 'undefined-slide-to-mute-v1',
            'sourceSha256': hashlib.sha256(source_path.read_bytes()).hexdigest(),
            'timeDomain': 'score_seconds', 'gestures': deepcopy(performance['undefinedSlideEvidence'])}
