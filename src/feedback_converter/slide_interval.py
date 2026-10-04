"""Authored interval for a targeted slide, relative to its merged attack."""
import math


def validate(note):
    interval = note.get('slide_interval')
    if interval is None and 'slide_interval' not in note:
        return None
    if (not isinstance(interval, dict) or set(interval) != {'start', 'end'}
            or type(note.get('sl')) is not int or not 0 <= note['sl'] <= 48
            or type(note.get('f')) is not int or not 0 < note['f'] <= 48
            or note.get('mt') or note.get('slu', -1) != -1
            or any(type(v) not in (int, float) or not math.isfinite(v)
                   for v in (interval.get('start'), interval.get('end'), note.get('sus')))
            or not 0 <= interval['start'] < interval['end'] <= note['sus'] + .0000011):
        raise ValueError('Invalid targeted slide interval.')
    return dict(interval)


def authored(articulation, destination, destination_start, at):
    """Only a continuous held fret with a terminal, immediately resolved link.

    A rest, displaced strum, changing fret or earlier slide cannot establish
    this interval. Leave those on their existing interpretation.
    """
    segments = articulation['bend_segments']
    first = segments[0][0]
    last, start, end, _ = segments[-1]
    if (last.slide not in {'shift', 'legato'} or not 0 < first.fret <= 24
            or not 0 <= destination.fret <= 24 or destination.effects.get('mt')
            or destination_start != end or end != articulation['end']
            or destination.voice_id != first.voice_id or destination.string != first.string
            or segments[0][1] != articulation['start']):
        return None
    for i, (n, left, right, _) in enumerate(segments):
        if ((n.fret, n.string, n.voice_id) != (first.fret, first.string, first.voice_id)
                or (i and (not n.tie or left != segments[i-1][2]))
                or n.staccato or n.attack_offset or n.effects.get('mt')
                or (n.slide and i != len(segments)-1)):
            return None
    return {'start': at(start)-at(articulation['start']), 'end': at(end)-at(articulation['start'])}
