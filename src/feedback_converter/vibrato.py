"""Timed finger-vibrato instructions; never a pitch/scoring curve."""
import math


def validate_marks(marks, sustain):
    if (not isinstance(marks, list) or len(marks) > 100000
            or type(sustain) not in (int, float) or not math.isfinite(sustain) or sustain < 0):
        raise ValueError('Vibrato marks must be an array of intervals.')
    previous = 0
    for mark in marks:
        if (not isinstance(mark, dict) or set(mark) != {'start', 'end', 'intensity'}
                or mark['intensity'] not in ('slight', 'wide')
                or any(type(mark[k]) not in (int, float) or not math.isfinite(mark[k]) for k in ('start', 'end'))
                or not previous <= mark['start'] < mark['end'] <= sustain + 0.0000011):
            raise ValueError('Invalid finger-vibrato interval.')
        previous = mark['end']
    return [dict(m) for m in marks]


def slice_marks(marks, start, end):
    """Crop a note-relative window, then rebase it to the new attack."""
    return [{**m, 'start': max(m['start'], start)-start, 'end': min(m['end'], end)-start}
            for m in marks if m['start'] < end and m['end'] > start]
