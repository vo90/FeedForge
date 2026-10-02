"""Retain incoming-slide markings on unpitched, visual-only scrapes."""
from copy import deepcopy
import hashlib


def record(note, track, occurrence, attack, start, end, scrape):
    _, pi, mi, vi, bi, ni = note.source_id.split(':')
    return {'trackId': track.id, 'sourceId': note.source_id,
            'location': f'parts/{pi}/measures/{mi}/voices/{vi}/beats/{bi}/notes/{ni}',
            'occurrence': occurrence + 1, 'attack': attack, 'start': start, 'end': end,
            'string': note.string, 'authored': {'slide': 'below' if note.slide_in == 'up' else 'above'},
            'used': {'rule': 'unpitched-scrape-no-pitched-entry', 'scrapeDirection': scrape}}


def report_findings(performance, report):
    from .compatibility import add_finding
    for row in performance.get('scrapeEntryEvidence', []):
        add_finding(report, feature='note.scrape_entry', category='game_limitation',
                    impact='display_or_expression',
                    message='The muted pick scrape is visual only and not scored. Its incoming-slide marking is retained in the source and report; no pitched slide or destination fret is implied.',
                    location=row['location'] + f"@visit{row['occurrence']}",
                    value={k: row[k] for k in ('authored', 'used')}, trackId=row['trackId'])


def archive_evidence(performance, source_path):
    return {'version': 1, 'policy': 'unpitched-scrape-entry-v1',
            'sourceSha256': hashlib.sha256(source_path.read_bytes()).hexdigest(),
            'timeDomain': 'score_seconds', 'gestures': deepcopy(performance['scrapeEntryEvidence'])}
