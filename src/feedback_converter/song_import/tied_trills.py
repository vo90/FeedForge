"""Project unambiguous native tied-trill events onto existing HO/PO notes."""
from fractions import Fraction as F


def sequence(state, main, fail):
    segments = state['bend_segments']
    if any(n.staccato for n, *_ in segments):
        fail('tied trill with staccato needs separate timing verification.')
    start, end = state['start'], state['end']
    events = [(start, True, main), (end, False, main)]
    retained = []
    for i, (note, left, right, occurrence) in enumerate(segments):
        spec = note.trill
        if not spec:
            continue
        if i == 0:
            left, right = start, end  # Native ties extend the initial marked note.
        step = F(spec['ticks'], spec['tpqn'])
        # Native release is one tick before the musical endpoint. It matters
        # for the short-note threshold, but never inserts a gap into gameplay.
        length = right - left - F(1, spec['tpqn'])
        count = 1 if length < step * F(3, 2) else max(2, int((right-left)/step))
        if len(events) + 2 * count > 1000000:
            fail('tied trill expansion exceeds the performed-note limit.')
        for k in range(1, count):
            when = left + k * step
            before = main if k % 2 else spec['auxiliaryFret']
            after = spec['auxiliaryFret'] if k % 2 else main
            events.extend([(when, False, before), (when, True, after)])
        if count > 1 and count % 2 == 0:
            events.append((right, False, spec['auxiliaryFret']))
        retained.append({'sourceId': note.source_id, 'occurrence': occurrence + 1,
                         'startQuarter': str(left), 'endQuarter': str(right),
                         'tied': note.tie, **spec})
    # Keep source insertion order within each time, like the native emitter.
    # Never merge two attacks, even at the same pitch.
    events.sort(key=lambda e: e[0])
    active, points = set(), []
    index = 0
    while index < len(events):
        when = events[index][0]
        onsets = []
        while index < len(events) and events[index][0] == when:
            _, attack, fret = events[index]
            if attack:
                if fret in active:
                    fail('tied trill produces an ambiguous repeated pitch; no reattack was invented.')
                active.add(fret)
                onsets.append(fret)
            else:
                active.discard(fret)
            index += 1
        if len(active) > 1 or len(onsets) > 1:
            fail('tied trill produces overlapping pitches; no source event was removed.')
        if not active and when < end:
            fail('tied trill leaves a silent interval inside the tie; no pitch was prolonged.')
        if onsets:
            if points and points[-1][1] == onsets[0]:
                fail('tied trill repeats the same fret; its reattack is ambiguous.')
            points.append((when, onsets[0]))
    if active or not points or points[0] != (start, main):
        fail('tied trill does not form a complete pitched sequence.')
    return [(left, points[i+1][0] if i+1 < len(points) else end, fret)
            for i, (left, fret) in enumerate(points)], retained
