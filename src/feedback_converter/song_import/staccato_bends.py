"""Resolve verified Songsterr finger bends after the complete tie is known."""
from copy import deepcopy
from fractions import Fraction as F
import hashlib

from .model import ScoreImportError


def finish(output, articulation, track_id, at, tempo_positions):
    segments = articulation['bend_segments']
    if (len(segments) < 2 or not any(n.staccato for n, *_ in segments)
            or not any(n.bends for n, *_ in segments)):
        return None
    if any(n.slide or n.slide_in or n.whammy or n.attack_offset for n, *_ in segments):
        raise ScoreImportError('Staccato ties with these pitch gestures need additional timing verification.')
    attack, stop = articulation['start'], articulation['end']
    curve, evidence = [], []
    previous_right = None

    def value_at(knots, q):
        left = knots[0]
        for right in knots[1:]:
            if right[0] > q:
                if q <= left[0]:
                    return left[1]
                return left[1] + (right[1] - left[1]) * float((q - left[0]) / (right[0] - left[0]))
            left = right
        return left[1]

    for i, (note, start, end, occurrence) in enumerate(segments):
        row = {'sourceId': note.source_id, 'occurrence': occurrence + 1,
               'start': at(start), 'end': at(end), 'staccato': note.staccato,
               'bend': [{'position': str(p), 'value': v} for p, v in note.bends]}
        evidence.append(row)
        if not note.bends:
            continue
        following = segments[i + 1] if i + 1 < len(segments) else None
        if following and following[0].bends:
            right = following[1]
        elif i == 0:
            right = stop
        else:
            right = start + max((end - start) / 2, F(1, 32)) if note.staccato else end
        if right <= start:
            raise ScoreImportError('A tied staccato bend has no verified gesture interval.')
        row['gestureEnd'] = at(right)
        row['audible'] = start < stop
        if start >= stop:
            continue
        if previous_right is not None and start < previous_right:
            raise ScoreImportError('Overlapping tied staccato bend controls need additional timing verification.')
        previous_right = min(right, stop)
        knots = [(start + (right - start) * p, v) for p, v in note.bends]
        left, clipped_right = max(start, attack), min(right, stop)
        if not curve and left > attack:
            curve.append({'t': 0.0, 'v': 0.0})
        if curve and curve[-1]['t'] < at(left) - output['t']:
            # A gap between control intervals holds the last pitch. An explicit
            # later prebend must not become a gradual, earlier pitch change.
            curve.append({'t': at(left) - output['t'], 'v': curve[-1]['v']})
        # Keep a linear curve in musical time through changes of tempo. The
        # later audio mapper similarly inserts its own synchronization anchors.
        positions = sorted({left, clipped_right, *[q for q, _ in knots if left < q < clipped_right],
                            *[q for q in tempo_positions if left < q < clipped_right]})
        for q in positions:
            point = {'t': at(q) - output['t'], 'v': value_at(knots, q)}
            if not curve or point != curve[-1]:
                curve.append(point)
    output.pop('bnv', None)
    output.pop('bn', None)
    if curve:
        output['bnv'] = curve
        output['bn'] = max((p['v'] for p in curve), key=abs)
    source_id = segments[0][0].source_id
    _, part, bar, voice, beat, ni = source_id.split(':')
    return {'trackId': track_id, 'sourceId': source_id,
            'location': f'parts/{part}/measures/{bar}/voices/{voice}/beats/{beat}/notes/{ni}',
            'occurrence': segments[0][3] + 1, 'start': at(attack), 'end': at(stop),
            'writtenEnd': at(segments[-1][2]), 'string': output['s'], 'fret': output['f'],
            'rule': 'tie-then-staccato-finger-bend', 'segments': evidence, 'curve': deepcopy(curve)}


def archive_evidence(performance, source_path):
    return {'version': 1, 'policy': 'songsterr-staccato-bends-v1',
            'timeDomain': 'score_seconds',
            'sourceSha256': hashlib.sha256(source_path.read_bytes()).hexdigest(),
            'chains': deepcopy(performance['staccatoBendEvidence'])}
