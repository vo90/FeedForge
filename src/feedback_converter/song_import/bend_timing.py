"""Resolve ordinary Songsterr finger bends on the completed tie timeline.

Mixed pitch gestures keep their existing interpretation, with an explicit
limitation. The established tied-staccato resolver retains its stricter guards.
"""
from bisect import bisect_right, bisect_left
from copy import deepcopy
import hashlib


def finish(output, articulation, track_id, at, tempo_positions):
    segments = articulation['bend_segments']
    if not any(n.bends for n, *_ in segments):
        return None
    if len(segments) > 1 and any(n.staccato for n, *_ in segments):
        return None  # Independently covered by the tied-staccato policy.
    attack, stop = articulation['start'], articulation['end']
    boundaries = tempo_positions[bisect_right(tempo_positions, attack):bisect_left(tempo_positions, stop)]
    if len(segments) == 1 and not boundaries:
        return None
    source_id = segments[0][0].source_id
    _, part, bar, voice, beat, ni = source_id.split(':')
    evidence = {'trackId': track_id, 'sourceId': source_id,
                'location': f'parts/{part}/measures/{bar}/voices/{voice}/beats/{beat}/notes/{ni}',
                'occurrence': segments[0][3] + 1, 'start': at(attack), 'end': at(stop),
                'string': output['s'], 'fret': output['f'], 'segments': []}
    intervals = []
    previous_end = None
    reason = None
    for i, (note, start, end, visit) in enumerate(segments):
        row = {'sourceId': note.source_id, 'occurrence': visit + 1,
               'start': at(start), 'end': at(end),
               'bend': [{'position': str(p), 'value': v} for p, v in note.bends]}
        evidence['segments'].append(row)
        if note.slide or note.slide_in or note.whammy or note.attack_offset:
            reason = 'mixed-pitch-or-displaced-attack'
        if not note.bends:
            continue
        following = segments[i + 1] if i + 1 < len(segments) else None
        right = following[1] if following and following[0].bends else stop if i == 0 else end
        row['gestureEnd'] = at(right)
        if previous_end is not None and start < previous_end:
            reason = reason or 'overlapping-bend-controls'
        previous_end = min(right, stop)
        intervals.append((note, start, right))
    if reason:
        return {**evidence, 'status': 'deferred', 'reason': reason,
                'rule': 'retained-segment-timing', 'curve': deepcopy(output.get('bnv', []))}

    curve = []
    for note, start, right in intervals:
        if start >= stop:
            continue
        left, right_edge = max(start, attack), min(right, stop)
        controls = [(start + (right - start) * p, v) for p, v in note.bends]

        def value_at(q):
            before = controls[0]
            for after in controls[1:]:
                if after[0] > q:
                    return before[1] if q <= before[0] else before[1] + (after[1] - before[1]) * float((q-before[0])/(after[0]-before[0]))
                before = after
            return before[1]

        if not curve and left > attack:
            curve.append({'t': 0., 'v': 0.})
        if curve and curve[-1]['t'] < at(left) - output['t']:
            curve.append({'t': at(left) - output['t'], 'v': curve[-1]['v']})
        # Preserve both sides of an authored discontinuity; extra tempo knots
        # interpolate in quarter-note time before converting to seconds.
        knots = [(q, v) for q, v in controls if left <= q <= right_edge]
        positions = {left, right_edge, *[q for q in boundaries if left < q < right_edge]}
        existing = {q for q, _ in knots}
        knots += [(q, value_at(q)) for q in positions - existing]
        for q, value in sorted(knots, key=lambda pair: pair[0]):
            point = {'t': at(q) - output['t'], 'v': value}
            if not curve or point != curve[-1]:
                curve.append(point)
    output['bnv'] = curve
    output['bn'] = max((p['v'] for p in curve), key=abs)
    return {**evidence, 'status': 'resolved', 'rule': 'tie-resolved-finger-bend', 'curve': deepcopy(curve)}


def archive_evidence(performance, source_path):
    return {'version': 1, 'policy': 'songsterr-finger-bend-timing-v1',
            'timeDomain': 'score_seconds',
            'sourceSha256': hashlib.sha256(source_path.read_bytes()).hexdigest(),
            'gestures': deepcopy(performance['fingerBendTimingEvidence'])}


def report_findings(performance, report):
    from .compatibility import add_finding
    for row in performance.get('fingerBendTimingEvidence', []):
        if row['status'] == 'deferred':
            add_finding(report, feature='note.bend_timing', category='conversion_check',
                        impact='display_or_expression', location=row['location'] + f"@visit{row['occurrence']}",
                        trackId=row['trackId'], value={'reason': row['reason']},
                        message='This bend combines overlapping controls or another pitch gesture. Its existing segment timing is retained, but matching Songsterr playback has not yet been verified. The complete source is preserved.')
