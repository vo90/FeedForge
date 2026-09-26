"""Resolve redundant fret encodings on otherwise unambiguous dead-note ties."""
from copy import deepcopy
import hashlib


def plain_segment(note):
    return not (note.bends or note.hopo or note.slide or note.slide_in
                or note.pick_scrape or note.whammy or note.trill
                or any(note.effects.get(k) for k in
                       ('__hopo_origin', 'hm', 'hp', 'hn', 'harmonic_target', 'vb')))


def equivalent(output, note, articulation):
    # 127 is the parser's explicit dead-without-fret sentinel. No other fret
    # difference, pitched note, or ambiguous gesture is normalized here.
    return (output.get('mt') is True and note.effects.get('mt') is True
            and {output['f'], note.fret} == {0, 127}
            and plain_segment(note) and not articulation['pitch_gesture']
            and not articulation['trill']
            and not any(output.get(k) for k in
                        ('hm', 'hp', 'hn', 'harmonic_target', 'vb', 'ho', 'po',
                         'ln', 'pick_scrape_marks', 'slide_out_marks', 'slide_in_marks')))


def record(output, note, track, occurrence, position, end, at):
    _, pi, bi, vi, beat, ni = note.source_id.split(':')
    return {'trackId': track.id, 'sourceId': note.source_id,
            'originSourceId': output['source_ids'][0],
            'location': f'parts/{pi}/measures/{bi}/voices/{vi}/beats/{beat}/notes/{ni}',
            'occurrence': occurrence + 1, 'attack': output['t'],
            'start': at(position), 'end': at(end), 'string': note.string,
            'authored': {'dead': True, 'fret': None if note.fret == 127 else note.fret},
            'used': {'dead': True, 'fret': None if output['f'] == 127 else output['f']},
            'rule': 'muted-tie-keeps-attack-target'}


def archive_evidence(performance, source_path):
    return {'version': 1, 'policy': 'songsterr-muted-tie-identity-v1',
            'sourceSha256': hashlib.sha256(source_path.read_bytes()).hexdigest(),
            'timeDomain': 'score_seconds',
            'continuations': deepcopy(performance['mutedTieIdentityEvidence'])}
