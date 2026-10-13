"""Independent qualification of plain tied continuations through written rests.

This module reads independent source facts, never the producer's helper,
normalized notes, or saved policy receipt.
"""
from bisect import bisect_left, bisect_right
from fractions import Fraction


def plain_beat(beat):
    """Retain a small independent fact about controls omitted on rest atoms."""
    allowed = {'duration', 'notes', 'rest', 'type', 'dots', 'dotted', 'doubleDotted',
               'tuplet', 'tupletStart', 'tupletStop', 'beamStart', 'beamStop',
               'text', 'chord', 'tempo', 'octaveClef'}
    for key, value in beat.items():
        if key in allowed or value is None or value is False:
            continue
        if key == 'tremoloBar' and type(value) is bool:
            continue
        return False
    return all(isinstance(note, dict) and all(
        key in {'string', 'fret', 'tie', 'rest', 'grace'} or value is None or value is False
        for key, value in note.items()) for note in beat.get('notes', []))


def source_id(location):
    path = location.split('/')
    return 'songsterr:' + ':'.join(path[i] for i in range(1, len(path), 2))


def _plain(atom):
    return (0 <= atom.fret <= 48
            and not (atom.bends or atom.slide or atom.slide_in or atom.hopo_origin
                     or atom.hopo_destination or atom.pick_scrape or atom.whammy
                     or atom.trill or atom.staccato or atom.attack_offset
                     or atom.pitch_offset or atom.finger_vibrato or atom.wide_vibrato
                     or atom.beat_vibrato or atom.tremolo_rate or atom.strum_direction)
            and not any(value is not None and value is not False for value in atom.effects.values()))


class Context:
    def __init__(self, part, order, clock):
        self.part, self.order, self.clock = part, order, clock
        self.routes = [0]
        for before, after in zip(order, order[1:]):
            self.routes.append(self.routes[-1] + (after != before + 1))
        voices = {}
        for occurrence, index in enumerate(order):
            offset = clock.measure_starts[occurrence]
            for beat in [*part.beats[index], *part.rest_tie_voice_rests.get(index, ())]:
                row = {'beat': beat, 'start': offset + beat['q'],
                       'end': offset + beat['q'] + beat['length'],
                       'occurrence': occurrence + 1}
                voices.setdefault(beat['voice'], []).append(row)
        self.voices = {}
        for voice, rows in voices.items():
            rows.sort(key=lambda row: (row['start'], row['end'], row['beat']['location']))
            bad, rest_rows = [], []
            for row in rows:
                if not row['beat'].get('rest_tie_plain', False):
                    bad.append(row)
                if row['beat']['rest']:
                    rest_rows.append(row)
            bad_ends, end = [], Fraction(-1)
            for row in bad:
                end = max(end, row['end'])
                bad_ends.append(end)
            rest_ends, end = [], Fraction(-1)
            for row in rest_rows:
                end = max(end, row['end'])
                rest_ends.append(end)
            self.voices[voice] = {'bad_starts': [r['start'] for r in bad], 'bad_ends': bad_ends,
                                  'rests': rest_rows, 'rest_starts': [r['start'] for r in rest_rows],
                                  'rest_ends': rest_ends}

    def plain_interval(self, voice, left, right):
        index = self.voices.get(voice)
        if index is None:
            return False
        last = bisect_left(index['bad_starts'], right) - 1
        return last < 0 or index['bad_ends'][last] <= left

    def rest_gap(self, voice, left, right):
        if right <= left or voice not in self.voices:
            return []
        index = self.voices[voice]
        stop = bisect_left(index['rest_starts'], right)
        first = bisect_right(index['rest_ends'], left, 0, stop)
        return [row for row in index['rests'][first:stop] if row['end'] > left]

    def admit(self, atom, event, occurrence, start, end, *, unique, pending):
        if (event is None or not atom.explicit_tie or not unique or pending
                or not event.get('unique_origin') or not event.get('history_unique')
                or event['end'] > start or not 0 <= event['f'] <= 48
                or not _plain(atom)):
            return None
        if (any(event.get(key) for key in ('linked', 'curve', 'whammy', 'trill', 'scrapes',
                                         'slide_marks', 'incoming_marks', 'targeted_slide',
                                         'contact', 'harmonic_conflict', 'muted_identity',
                                         'bent_identity', 'pending_slide_identity'))
                or any(value is not None and value is not False for value in event['effects'].values())):
            return None
        marked = event.get('rest_identity', False)
        rests = self.rest_gap(atom.voice, event['end'], start)
        if not marked and not rests:
            return None
        if any(r['start'] < event['end'] or r['end'] > start for r in rests):
            return None
        history = event.get('bend_atoms', ())
        if not history:
            return None
        first, attack, _first_end, origin_visit = history[0]
        if (first.tie or attack != event['start'] or first.location != event['locations'][0]
                or (first.voice, first.string) != (atom.voice, atom.string)
                or origin_visit > occurrence or self.routes[origin_visit] != self.routes[occurrence]):
            return None
        if not marked:
            for i, (prior, left, right, _visit) in enumerate(history):
                if (not _plain(prior) or (prior.voice, prior.string) != (atom.voice, atom.string)
                        or i and (not prior.explicit_tie or left < history[i - 1][2])):
                    return None
        examined_start = event['end'] if marked else event['start']
        if not self.plain_interval(atom.voice, examined_start, end):
            return None
        def time(value):
            return float(self.clock.at(value))
        return {'trackId': self.part.id, 'sourceId': source_id(atom.location),
                'originSourceId': source_id(first.location), 'location': atom.location,
                'occurrence': occurrence + 1, 'originOccurrence': origin_visit + 1,
                'voice': int(atom.voice), 'string': atom.string,
                'attack': time(event['start']), 'previousEnd': time(event['end']),
                'start': time(start), 'end': time(end),
                'authored': {'fret': atom.fret}, 'used': {'fret': event['f']},
                'rule': 'plain-tie-continues-through-rests', 'bridgesRest': bool(rests),
                'rests': [{'sourceId': source_id(r['beat']['location']), 'location': r['beat']['location'],
                           'occurrence': r['occurrence'], 'start': time(r['start']), 'end': time(r['end']),
                           'quarters': {'start': str(r['start']), 'end': str(r['end'])}} for r in rests]}
