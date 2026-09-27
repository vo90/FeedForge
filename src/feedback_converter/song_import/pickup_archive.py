"""Exact display clock for short bars, including those without pitched notation."""
from bisect import bisect_right
import hashlib

from .alignment import map_time


def archive(performance, alignment, source_path):
    timeline = performance.get('scoreTimeline', {})
    pickups = [m for m in timeline.get('measures', []) if m.get('pickup')]
    if not pickups:
        return None
    points = timeline['tempoPoints']
    def quarter(t):
        p = points[max(0, bisect_right(points, t, key=lambda p: p['time']) - 1)]
        return p['quarter'] + (t - p['time']) * p['bpm'] / 60
    rows = []
    for bar in pickups:
        left, right = bar['start'], bar['end']
        times = sorted({left, right,
                        *(p['time'] for p in points if left < p['time'] < right),
                        *(p['score'] for p in alignment.get('anchors', []) if left + 1e-9 < p['score'] < right - 1e-9)})
        rows.append({'occurrence': bar['index'], 'signature': [bar['numerator'], bar['denominator']],
                     'quarters': bar['quarters'],
                     'anchors': [{'time': round(map_time(alignment, t, allow_negative=True), 6),
                                  'quarter': round(quarter(t) - bar['quarter'], 9)} for t in times]})
    return {'version': 1, 'timeDomain': 'recording_seconds',
            'sourceSha256': hashlib.sha256(source_path.read_bytes()).hexdigest(), 'measures': rows}
