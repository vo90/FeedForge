"""Independent rational reconstruction; no producer imports or curve reuse."""
from bisect import bisect_left, bisect_right
from fractions import Fraction as F


def check_evidence(wanted, actual, check, path='import/finger-bend-timing', field=None):
    """Exact structure/identities; bounded numeric error, never rounded equality.

    Subtracting two large float timestamps can straddle a rounding half-unit
    even when the underlying rational and floating clocks agree.
    """
    code = 'finger_bend_timing'
    if isinstance(wanted, dict):
        if not isinstance(actual, dict):
            check.fail(code, path, 'Expected a bend evidence object.')
            return
        check.equal(code, path + '/keys', sorted(wanted), sorted(actual))
        for key, value in wanted.items():
            check_evidence(value, actual.get(key), check, path + '/' + key, key)
    elif isinstance(wanted, list):
        if not isinstance(actual, list):
            check.fail(code, path, 'Expected a bend evidence array.')
            return
        check.equal(code, path + '/count', len(wanted), len(actual))
        for i, (a, b) in enumerate(zip(wanted, actual)):
            check_evidence(a, b, check, path + '/' + str(i))
    elif field in {'start', 'end', 'gestureEnd', 't'}:
        check.near(code, path, wanted, actual)
    elif field in {'v', 'value'}:
        check.near(code, path, wanted, actual, 1e-9)
    else:
        if type(wanted) is not type(actual):
            check.fail(code, path, 'Bend evidence identity type differs.')
        else:
            check.equal(code, path, wanted, actual)


def reconstruct(event, part, clock, sound_end):
    entries = event['bend_atoms']
    if not any(a.bends for a, *_ in entries):
        return None
    if len(entries) > 1 and any(a.staccato for a, *_ in entries):
        return None
    origin = event['start']
    boundaries = clock.positions[bisect_right(clock.positions, origin):bisect_left(clock.positions, sound_end)]
    if len(entries) == 1 and not boundaries:
        return None

    def identity(atom):
        fields = atom.location.split('/')
        return 'songsterr:' + ':'.join(fields[i] for i in (1, 3, 5, 7, 9))

    rows, gestures = [], []
    mixed = any(a.slide or a.slide_in or a.whammy or a.attack_offset for a, *_ in entries)
    overlap = False
    crossings = []
    last_end = None
    for i, (atom, start, end, visit) in enumerate(entries):
        row = {'sourceId': identity(atom), 'occurrence': visit + 1,
               'start': float(clock.at(start)), 'end': float(clock.at(end)),
               'bend': [{'position': str(p), 'value': float(v)} for p, v in atom.bends]}
        rows.append(row)
        if not atom.bends:
            continue
        stop = sound_end if i == 0 else end
        if i + 1 < len(entries) and entries[i + 1][0].bends:
            stop = entries[i + 1][1]
        row['gestureEnd'] = float(clock.at(stop))
        if last_end is not None and start < last_end:
            overlap = True
            crossings.append((len(gestures)-1, start))
        last_end = min(stop, sound_end)
        gestures.append((atom, start, stop))

    first = entries[0][0]
    evidence = {'trackId': part.id, 'sourceId': identity(first), 'location': first.location,
                'occurrence': entries[0][3] + 1, 'start': float(clock.at(origin)), 'end': float(clock.at(sound_end)),
                'string': event['s'], 'fret': event['f'], 'segments': rows}
    overlap_reason = None
    if overlap:
        compound = any(a.hopo_origin or a.hopo_destination or a.trill or a.pick_scrape or
                       any(a.effects.get(k) for k in ('vb', 'hm', 'hp', 'hn', 'harmonic_target',
                                                      'mt', 'lr', 'pm', 'tr')) for a, *_ in entries)
        handoffs = []
        for index, q in crossings:
            atom, begin, finish = gestures[index]
            points = [(begin+(finish-begin)*p, v) for p,v in atom.bends]
            # The suffix must already be flat, including the segment crossing
            # the handoff. The remaining terminal update must be at note-off.
            flat = points[0][0] <= q and all(
                b[1] == a[1] for a,b in zip(points,points[1:]) if b[0] > q)
            safe = flat and finish == sound_end
            handoffs.append({'sourceId': identity(atom), 'nextSourceId': identity(gestures[index+1][0]),
                             'start': float(clock.at(q)), 'end': float(clock.at(finish)),
                             'classification': 'settled-tail' if safe else 'changing-tail'})
        label = ('other-expression' if mixed or compound else
                 'clear-handoff' if all(h['classification']=='settled-tail' for h in handoffs)
                 else 'conflicting-controls')
        evidence['overlap'] = {'classification': label, 'handoffs': handoffs}
        if label != 'clear-handoff':
            overlap_reason = 'overlap-with-other-expression' if compound else 'overlapping-bend-controls'
    if mixed or overlap_reason:
        return {**evidence, 'status': 'deferred', 'rule': 'retained-segment-timing',
                'reason': 'mixed-pitch-or-displaced-attack' if mixed else overlap_reason,
                'curve': [{'t': float(q-clock.at(origin)), 'v': float(v)} for q,v in event['curve']]}

    knots = []
    for i, (atom, begin, finish) in enumerate(gestures):
        if begin >= sound_end:
            continue
        lo, hi = max(origin, begin), min(sound_end, finish)
        if i + 1 < len(gestures):
            hi = min(hi, gestures[i+1][1])
        controls = [(begin + (finish-begin)*p, v) for p,v in atom.bends]
        if lo > origin and not knots:
            knots.append((clock.at(origin), F(0)))
        if knots and knots[-1][0] < clock.at(lo):
            knots.append((clock.at(lo), knots[-1][1]))
        samples = [(q,v) for q,v in controls if lo <= q <= hi]
        extra = {lo, hi, *[q for q in boundaries if lo < q < hi]} - {q for q,v in samples}
        for q in extra:
            earlier = [pair for pair in controls if pair[0] <= q]
            later = [pair for pair in controls if pair[0] > q]
            if not earlier:
                v = controls[0][1]
            elif not later:
                v = controls[-1][1]
            else:
                a,b = earlier[-1], later[0]
                v = a[1] + (b[1]-a[1])*(q-a[0])/(b[0]-a[0])
            samples.append((q,v))
        for q,v in sorted(samples, key=lambda pair: pair[0]):
            point = (clock.at(q), v)
            if not knots or knots[-1] != point:
                knots.append(point)
    event['curve'] = knots
    return {**evidence, 'status': 'resolved', 'rule': 'settled-bend-handoff' if overlap else 'tie-resolved-finger-bend',
            'curve': [{'t': float(q-clock.at(origin)), 'v': float(v)} for q,v in knots]}
