"""Independent display-clock check from rational source measures and tempos."""
from bisect import bisect_right
from .verify_source import fraction
from .verify_timeline import Clock, RecordingMap, visits


def verify(source, alignment, retained, digest, check):
    order = visits(source)
    clock, recording = Clock(source, order), RecordingMap(alignment)
    rows = []
    for occurrence, index in enumerate(order):
        bar = source.bars[index]
        if not bar.pickup:
            continue
        q = clock.measure_starts[occurrence]
        left, right = clock.at(q), clock.at(q + bar.length)
        points = {left: q, right: q + bar.length}
        points.update({clock.at(p): p for p in clock.positions if q < p < q + bar.length})
        for anchor in alignment.get('anchors', []):
            t = fraction(anchor['score'])
            # Serialized float bar boundaries can sit a few ulps inside the
            # independently rational boundary; they are not extra segments.
            if left + fraction(1e-9) < t < right - fraction(1e-9):
                at = max(0, bisect_right(clock.seconds, t) - 1)
                points[t] = clock.positions[at] + (t - clock.seconds[at]) * clock.bpms[at] / 60
        rows.append({'occurrence': occurrence, 'signature': list(bar.signature), 'quarters': float(bar.length),
                     'anchors': [{'time': recording.at(t), 'quarter': float(p - q)} for t,p in sorted(points.items())]})
    check.equal('pickup_clock', 'pickup/version', 1, retained.get('version'))
    check.equal('pickup_clock', 'pickup/timeDomain', 'recording_seconds', retained.get('timeDomain'))
    check.equal('pickup_clock', 'pickup/sourceSha256', digest, retained.get('sourceSha256'))
    actual = retained.get('measures', [])
    check.equal('pickup_clock', 'pickup/count', len(rows), len(actual))
    for i, (wanted, value) in enumerate(zip(rows, actual)):
        for k in ('occurrence', 'signature'):
            check.equal('pickup_clock', f'pickup/{i}/{k}', wanted[k], value.get(k))
        check.near('pickup_clock', f'pickup/{i}/quarters', wanted['quarters'], value.get('quarters'), 1e-8)
        anchors = value.get('anchors', [])
        check.equal('pickup_clock', f'pickup/{i}/anchors', len(wanted['anchors']), len(anchors))
        for j, (a,b) in enumerate(zip(wanted['anchors'], anchors)):
            for k in ('time', 'quarter'):
                check.near('pickup_clock', f'pickup/{i}/{j}/{k}', a[k], b.get(k), 1.1e-6 if k == 'time' else 1e-8)
