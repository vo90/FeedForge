"""Independent rational reconstruction of tied staccato finger bends.

Consumes verifier source atoms; never imports the producer or its timing helper.
"""
from fractions import Fraction as F

from .verify_source import fraction, unsupported


def reconstruct(event, part, clock, sound_end):
    entries = event['bend_atoms']
    if (len(entries) <= 1 or not any(a.staccato for a, *_ in entries)
            or not any(a.bends for a, *_ in entries)):
        return None
    if any(a.slide or a.slide_in or a.whammy or a.attack_offset for a, *_ in entries):
        unsupported(entries[0][0].location, 'Unverified pitch gesture on a staccato tie.')
    origin = event['start']
    rows, knots = [], []
    last_gesture_end = None

    def identity(atom):
        p = atom.location.split('/')
        return 'songsterr:' + ':'.join(p[i] for i in (1, 3, 5, 7, 9))

    for number, (atom, start, end, visit) in enumerate(entries):
        record = {'sourceId': identity(atom), 'occurrence': visit + 1,
                  'start': float(clock.at(start)), 'end': float(clock.at(end)),
                  'staccato': atom.staccato,
                  'bend': [{'position': str(p), 'value': float(v)} for p, v in atom.bends]}
        rows.append(record)
        if not atom.bends:
            continue
        note_off = sound_end if number == 0 else (start + max((end-start)/2, F(1,32)) if atom.staccato else end)
        if number + 1 < len(entries) and entries[number+1][0].bends:
            note_off = entries[number+1][1]
        if note_off <= start:
            unsupported(atom.location, 'Empty source bend interval after tie resolution.')
        record.update(gestureEnd=float(clock.at(note_off)), audible=start < sound_end)
        if start >= sound_end:
            continue
        if last_gesture_end is not None and start < last_gesture_end:
            unsupported(atom.location, 'Interleaved tied staccato bend controls are unverified.')
        last_gesture_end = min(note_off, sound_end)
        controls = [(start + (note_off-start)*p, fraction(v)) for p,v in atom.bends]
        lower, upper = max(origin, start), min(sound_end, note_off)
        if lower > origin and not knots:
            knots.append((clock.at(origin), F(0)))
        if knots and knots[-1][0] < clock.at(lower):
            knots.append((clock.at(lower), knots[-1][1]))
        coordinates = {lower, upper}
        coordinates.update(q for q,_ in controls if lower < q < upper)
        coordinates.update(q for q in clock.positions if lower < q < upper)
        for q in sorted(coordinates):
            before = [p for p in controls if p[0] <= q]
            after = [p for p in controls if p[0] > q]
            if not before:
                value = controls[0][1]
            elif not after:
                value = controls[-1][1]
            else:
                a,b = before[-1],after[0]
                value = a[1] + (b[1]-a[1])*(q-a[0])/(b[0]-a[0])
            point = (clock.at(q), value)
            if not knots or knots[-1] != point:
                knots.append(point)
    event['curve'] = knots
    first = entries[0][0]
    return {'trackId': part.id, 'sourceId': identity(first), 'location': first.location,
            'occurrence': entries[0][3] + 1, 'start': float(clock.at(origin)),
            'end': float(clock.at(sound_end)), 'writtenEnd': float(clock.at(entries[-1][2])),
            'string': event['s'], 'fret': event['f'], 'rule': 'tie-then-staccato-finger-bend',
            'segments': rows, 'curve': [{'t':float(q-clock.at(origin)), 'v':float(v)} for q,v in knots]}
