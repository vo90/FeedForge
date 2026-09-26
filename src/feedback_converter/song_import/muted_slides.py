"""Preserve unpitched slide instructions without manufacturing fret paths."""
from copy import deepcopy
import hashlib


def record(note, track, occurrence, attack, start, end):
    _, pi, mi, vi, bi, ni = note.source_id.split(':')
    return {'trackId': track.id, 'sourceId': note.source_id,
            'location': f'parts/{pi}/measures/{mi}/voices/{vi}/beats/{bi}/notes/{ni}',
            'occurrence': occurrence + 1, 'attack': attack, 'start': start,
            'end': end, 'string': note.string,
            'authored': {'slide': {'shift': 'shift', 'out_up': 'upwards', 'out_down': 'downwards'}[note.slide]},
            'used': {'rule': 'retained-shift-no-pitch-path' if note.slide == 'shift' else 'unscored-directional-slide'},
            'target': None}


def report_findings(performance, report):
    from .compatibility import add_finding
    for row in performance.get('mutedSlideEvidence', []):
        message = ('The muted shift and its destination are retained. The X and tie are preserved; no fret path is drawn because its starting fret is unspecified. The following pitched note remains playable.'
                   if row['authored']['slide'] == 'shift' else
                   'Muted slide-outs are visual only, not scored. Direction, strings and timing are preserved; an updated game is required to display them.')
        add_finding(report, feature='note.muted_slide', category='game_limitation',
                    impact='display_or_expression', message=message,
                    location=row['location'] + f"@visit{row['occurrence']}",
                    value={k: row[k] for k in ('authored', 'used')}, trackId=row['trackId'])


def archive_evidence(performance, source_path):
    return {'version': 1, 'policy': 'songsterr-muted-slides-v1',
            'sourceSha256': hashlib.sha256(source_path.read_bytes()).hexdigest(),
            'timeDomain': 'score_seconds', 'gestures': deepcopy(performance['mutedSlideEvidence'])}
