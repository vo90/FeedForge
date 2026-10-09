"""Validated visual tremolo intervals. They never introduce scored attacks."""
import math


def validate(marks, sustain):
    if type(sustain) not in (int,float) or not math.isfinite(sustain) or sustain < 0:
        raise ValueError('Invalid tremolo owning duration.')
    if not isinstance(marks, list) or len(marks) > 100000:
        raise ValueError('Invalid tremolo intervals.')
    last = 0
    for m in marks:
        if (not isinstance(m, dict) or set(m) != {'start', 'end', 'rate', 'period', 'phase', 'mode'}
                or any(type(m[k]) not in (int, float) or not math.isfinite(m[k]) for k in ('start', 'end', 'phase'))
                or not last <= m['start'] < m['end'] <= sustain + 1.1e-6
                or not 0 <= m['phase'] < 1):
            raise ValueError('Invalid tremolo interval bounds.')
        if m['mode'] == 'specified':
            if (not isinstance(m['rate'], list) or len(m['rate']) != 2
                    or any(type(v) is not int or v <= 0 for v in m['rate'])
                    or type(m['period']) not in (int, float) or not math.isfinite(m['period']) or m['period'] <= 0):
                raise ValueError('Invalid tremolo picking rate.')
        elif m['mode'] not in ('unspecified', 'conflicting') or m['rate'] is not None or m['period'] is not None or m['phase'] != 0:
            raise ValueError('Invalid generic tremolo interval.')
        last = m['end']
    return marks


def slice_marks(marks, left, right):
    result = []
    for m in marks:
        a, b = max(left, m['start']), min(right, m['end'])
        if a >= b: continue
        phase = (m['phase'] + (a-m['start'])/m['period']) % 1 if m['period'] else 0
        result.append({**m, 'start': round(a-left, 6), 'end': round(b-left, 6), 'phase': round(phase, 9) % 1})
    return result
