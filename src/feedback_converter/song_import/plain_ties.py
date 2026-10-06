"""Bind plain Songsterr continuations to the established pitched attack."""
from copy import deepcopy
import hashlib

POLICY = 'songsterr-plain-tie-identity-v1'


def admitted(output, note, articulation, document, *, linked=False, unique=True, uninterrupted=True):
    """Qualify a differing stored fret without repairing musical continuity."""
    if linked or not unique or not uninterrupted or articulation.get('source_unique') is False or not note.tie or not note.source_id:
        return False
    try:
        _, pi, mi, vi, bi, ni = note.source_id.split(':')
        raw = document['parts'][int(pi)]['measures'][int(mi)]['voices'][int(vi)]['beats'][int(bi)]['notes'][int(ni)]
    except (KeyError, IndexError, TypeError, ValueError):
        return False
    if raw.get('tie') is not True or not 0 <= note.fret <= 48 or not 0 <= output['f'] <= 48:
        return False
    harmonic = ('hm', 'hp', 'hn', 'hps', 'harmonic_target', 'harmonic_alias', 'harmonic_changes')
    if (output.get('mt') or output.get('pick_scrape_marks') or output.get('slide_out_marks') or output.get('slide_out')
            or any(output.get(k) for k in harmonic)
            or note.effects.get('mt') or any(note.effects.get(k) for k in (*harmonic, 'vb', '__hopo_origin'))
            or note.bends or note.hopo or note.slide or note.slide_in or note.pick_scrape
            or note.whammy or note.trill):
        return False
    # A completed incoming slide and origin vibrato keep one base target.
    # Other expressive origin combinations need their own qualification.
    if articulation.get('trill') or any(n.bends or n.whammy for n, *_ in articulation.get('bend_segments', [])):
        return False
    return True


def record(output, note, track, occurrence, position, end, at):
    _, pi, mi, vi, bi, ni = note.source_id.split(':')
    return {'trackId': track.id, 'sourceId': note.source_id,
            'originSourceId': output['source_ids'][0],
            'location': f'parts/{pi}/measures/{mi}/voices/{vi}/beats/{bi}/notes/{ni}',
            'occurrence': occurrence + 1, 'voice': int(note.voice_id),
            'string': note.string, 'attack': output['t'], 'start': at(position), 'end': at(end),
            'authored': {'fret': note.fret}, 'used': {'fret': output['f']},
            'rule': 'plain-tie-keeps-attack-target'}


def archive_evidence(performance, source_path):
    return {'version': 1, 'policy': POLICY,
            'sourceSha256': hashlib.sha256(source_path.read_bytes()).hexdigest(),
            'timeDomain': 'score_seconds',
            'continuations': deepcopy(performance['plainTieIdentityEvidence'])}
