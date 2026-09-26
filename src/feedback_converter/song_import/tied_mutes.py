"""Retain ambiguous late dead flags without rewriting a pitched attack."""
from copy import deepcopy
import hashlib


def retain(output, effects, note, track, occurrence, position, end, at, records):
    if (effects.get('mt') is not True or output.get('mt') or note.fret == 127
            or note.pick_scrape or output.get('pick_scrape_marks')):
        return False
    _, pi, bi, vi, beat, ni = note.source_id.split(':')
    records.append({
        'trackId': track.id, 'sourceId': note.source_id,
        'location': f'parts/{pi}/measures/{bi}/voices/{vi}/beats/{beat}/notes/{ni}',
        'occurrence': occurrence + 1, 'attack': output['t'],
        'start': at(position), 'end': at(end), 'string': note.string, 'fret': note.fret,
        'authored': {'dead': True}, 'used': {'dead': False},
        'rule': 'initial-pitched-target-continued'})
    return True


def report_findings(performance, report):
    from .compatibility import add_finding
    for row in performance.get('tiedMuteEvidence', []):
        add_finding(report, feature='note.tied_mute', category='game_limitation',
                    impact='display_or_expression',
                    message='A dead-note flag on a tied continuation is retained in the source. Gameplay keeps the established pitched sustain and pitch curves without inventing a new attack or cutoff.',
                    location=row['location'] + f"@visit{row['occurrence']}",
                    value={k: row[k] for k in ('authored', 'used', 'rule')},
                    trackId=row['trackId'])


def archive_evidence(performance, source_path):
    return {'version': 1, 'policy': 'songsterr-tied-mutes-v1',
            'sourceSha256': hashlib.sha256(source_path.read_bytes()).hexdigest(),
            'timeDomain': 'score_seconds',
            'continuations': deepcopy(performance['tiedMuteEvidence'])}
