"""Identify a targeted slide orphaned by a skipped ending, per visit."""
from bisect import bisect_left, bisect_right
from collections import defaultdict


def omissions(score, track, visits, at):
    if score.source.get('format') != 'songsterr' or not track.written_bars:
        return {}
    if not any(visits[v + 1][0] > bar + 1 and score.measures[bar + 1].endings
               for v, (bar, _) in enumerate(visits[:-1])):
        return {}
    # Index once. No unbounded per-slide search through a repeated song.
    notes, rests = defaultdict(list), defaultdict(list)
    for visit, (bar, start) in enumerate(visits):
        for note in track.bars[bar]:
            notes[note.voice_id, note.string].append(start + note.position)
        for voice in track.written_bars[bar]:
            for beat in voice.beats:
                if beat.rest and beat.duration > 0:
                    rests[str(voice.source_index)].append((start + beat.position, visit, beat.source_id))
    for values in notes.values():
        values.sort()
    for values in rests.values():
        values.sort()
    rest_starts = {voice: [r[0] for r in values] for voice, values in rests.items()}
    result = {}
    for visit, (bar, start) in enumerate(visits[:-1]):
        following = visits[visit + 1][0]
        if following <= bar + 1 or not score.measures[bar + 1].endings:
            continue
        for note in track.bars[bar]:
            if (note.slide not in ('shift', 'legato') or note.fret == 127 or note.effects.get('mt')
                    or note.position + note.duration != score.measures[bar].length):
                continue
            destinations = [n for n in track.bars[bar + 1]
                            if (n.voice_id, n.string) == (note.voice_id, note.string) and n.position == 0]
            if (len(destinations) != 1 or destinations[0].tie or destinations[0].fret == 127
                    or destinations[0].effects.get('mt')):
                continue
            end = start + note.position + note.duration
            spans = rests.get(note.voice_id, [])
            r = bisect_left(rest_starts.get(note.voice_id, []), end)
            if r == len(spans):
                continue
            rest_time, rest_visit, rest_id = spans[r]
            attacks = notes[note.voice_id, note.string]
            n = bisect_right(attacks, start + note.position)
            # A continuation or attack before (or at) the rest takes precedence.
            if n < len(attacks) and attacks[n] <= rest_time:
                continue
            target = destinations[0]
            _, pi, mi, vi, bi = rest_id.split(':')
            result[visit, note.source_id] = {
                'target': {'sourceId': target.source_id, 'measure': bar + 2,
                           'fret': target.fret, 'performed': False},
                'transition': {'fromMeasure': bar + 1, 'toMeasure': following + 1,
                               'occurrence': visit + 2},
                'interruption': {'location': f'parts/{pi}/measures/{mi}/voices/{vi}/beats/{bi}',
                                 'occurrence': rest_visit + 1, 'time': at(rest_time), 'kind': 'rest'},
                'used': {'rule': 'omit-slide-skipped-ending-rest'}}
    return result
