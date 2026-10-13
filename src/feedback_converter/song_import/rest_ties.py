"""Qualified Songsterr ties that hold one plain attack through written rests."""
from bisect import bisect_left, bisect_right
from copy import deepcopy
from fractions import Fraction
import hashlib

POLICY = 'songsterr-rest-tie-continuation-v1'
RULE = 'plain-tie-continues-through-rests'
BEAT_METADATA = frozenset({'duration', 'notes', 'rest', 'type', 'dots', 'dotted',
    'doubleDotted', 'tuplet', 'tupletStart', 'tupletStop', 'beamStart', 'beamStop',
    'text', 'chord', 'tempo', 'octaveClef'})
NOTE_METADATA = frozenset({'string', 'fret', 'tie', 'rest', 'grace'})


def _inactive(value):
    return value is None or value is False


def _payload_plain(raw, allowed, *, beat=False):
    return isinstance(raw, dict) and all(
        key in allowed or _inactive(value)
        or beat and key == 'tremoloBar' and type(value) is bool
        for key, value in raw.items())


def _source(document, source_id):
    try:
        prefix, pi, mi, vi, *tail = source_id.split(':')
        if prefix != 'songsterr':
            return None
        raw = document['parts'][int(pi)]['measures'][int(mi)]['voices'][int(vi)]
        if tail:
            raw = raw['beats'][int(tail[0])]
        if len(tail) > 1:
            raw = raw['notes'][int(tail[1])]
        return raw
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def _location(source_id):
    _, pi, mi, vi, *tail = source_id.split(':')
    result = f'parts/{pi}/measures/{mi}/voices/{vi}'
    if tail:
        result += f'/beats/{tail[0]}'
    if len(tail) > 1:
        result += f'/notes/{tail[1]}'
    return result


def _beat_plain(raw):
    # Controllers on a rest must not disappear merely because it has no Note.
    return (_payload_plain(raw, BEAT_METADATA, beat=True)
            and isinstance(raw.get('notes'), list)
            and all(_payload_plain(n, NOTE_METADATA) for n in raw['notes']))


def plain_note(note, document, *, tied):
    if (not 0 <= note.fret <= 48 or not note.source_id or not note.beat_id
            or note.bends or note.hopo or note.slide or note.slide_in or note.pick_scrape
            or note.whammy or note.trill or note.staccato or note.attack_offset
            or not _inactive(note.tremolo_rate)
            or any(not _inactive(v) for v in note.effects.values())):
        return False
    raw = _source(document, note.source_id)
    if not _payload_plain(raw, NOTE_METADATA) or not _beat_plain(_source(document, note.beat_id)):
        return False
    return raw.get('tie') is True if tied else _inactive(raw.get('tie'))


class Context:
    """Index performed rest spans, nonplain beats and route discontinuities."""
    def __init__(self, score, track, visits):
        document = score.source_document['document']
        rests, bad = {}, {}
        self.route = []
        discontinuities = 0
        previous = None
        for occurrence, (index, start) in enumerate(visits):
            if previous is not None and index != previous + 1:
                discontinuities += 1
            self.route.append(discontinuities)
            previous = index
            for voice in track.written_bars[index] if track.written_bars else []:
                key = str(voice.source_index)
                raw_voice = _source(document, voice.source_id)
                if not voice.beats and isinstance(raw_voice, dict) and raw_voice.get('rest') is True:
                    end = start + score.measures[index].length
                    rests.setdefault(key, []).append(self._span(voice.source_id, occurrence, start, end))
                    if not _payload_plain(raw_voice, {'beats', 'rest', 'hasSameRhythm'}):
                        bad.setdefault(key, []).append((start, end))
                for beat in voice.beats:
                    left, right = start + beat.position, start + beat.position + beat.duration
                    if beat.rest:
                        rests.setdefault(key, []).append(self._span(beat.source_id, occurrence, left, right))
                    if not _beat_plain(_source(document, beat.source_id)):
                        bad.setdefault(key, []).append((left, right))
        self.rests = {voice: self._index(rows) for voice, rows in rests.items()}
        self.bad = {voice: self._index([{'left':a, 'right':b} for a, b in rows]) for voice, rows in bad.items()}

    @staticmethod
    def _span(source_id, occurrence, left, right):
        return {'sourceId': source_id, 'location': _location(source_id),
                'occurrence': occurrence + 1, 'left': left, 'right': right}

    @staticmethod
    def _index(rows):
        rows.sort(key=lambda row: (row['left'], row['right'], row.get('sourceId', '')))
        starts, ends, stop = [], [], Fraction(-1)
        for row in rows:
            starts.append(row['left'])
            stop = max(stop, row['right'])
            ends.append(stop)
        return rows, starts, ends

    def gap_rests(self, voice, left, right):
        if right <= left or voice not in self.rests:
            return []
        rows, starts, ends = self.rests[voice]
        upper = bisect_left(starts, right)
        lower = bisect_right(ends, left, 0, upper)
        return [r for r in rows[lower:upper] if r['right'] > left]

    def plain_context(self, voice, left, right):
        if voice not in self.bad:
            return True
        _, starts, ends = self.bad[voice]
        index = bisect_left(starts, right) - 1
        return index < 0 or ends[index] <= left

    def consecutive(self, origin_occurrence, occurrence):
        return (0 <= origin_occurrence - 1 <= occurrence < len(self.route)
                and self.route[origin_occurrence - 1] == self.route[occurrence])


def admitted(output, note, articulation, document, context, *, occurrence,
             previous_end, position, end, unique, linked, rests):
    active = bool(articulation.get('rest_identity'))
    if (linked or articulation.get('linked') or not unique or articulation.get('source_unique') is False
            or articulation.get('pending_hopo_history_unique') is False
            or previous_end > position or not active and not rests
            or any(r['left'] < previous_end or r['right'] > position for r in rests)
            or not plain_note(note, document, tied=True)
            or not context.consecutive(articulation['occurrence'], occurrence)
            or any(not _inactive(v) for k, v in output.items()
                   if k not in {'t', 's', 'f', 'sus', 'source_ids'})):
        return False
    segments = articulation.get('bend_segments', [])
    if not segments:
        return False
    origin = segments[0][0]
    if (note.voice_id, note.string) != (origin.voice_id, origin.string):
        return False
    if not active:
        if any(not plain_note(n, document, tied=bool(i))
               or (n.voice_id, n.string) != (origin.voice_id, origin.string)
               or i and left < segments[i - 1][2]
               for i, (n, left, _right, _visit) in enumerate(segments)):
            return False
    # Initial qualification covers the complete held history. Thereafter every
    # continuation is checked, so only newly traversed context needs scanning.
    return context.plain_context(note.voice_id,
        previous_end if active else articulation['start'], end)


def record(output, note, track, occurrence, previous_end, position, end, at, articulation, rests):
    return {'trackId': track.id, 'sourceId': note.source_id,
            'originSourceId': output['source_ids'][0], 'location': _location(note.source_id),
            'occurrence': occurrence + 1, 'originOccurrence': articulation['occurrence'],
            'voice': int(note.voice_id), 'string': note.string, 'attack': output['t'],
            'previousEnd': at(previous_end), 'start': at(position), 'end': at(end),
            'authored': {'fret': note.fret}, 'used': {'fret': output['f']},
            'rule': RULE, 'bridgesRest': bool(rests),
            'rests': [{'sourceId': row['sourceId'], 'location': row['location'],
                       'occurrence': row['occurrence'], 'start': at(row['left']), 'end': at(row['right']),
                       'quarters': {'start': str(row['left']), 'end': str(row['right'])}}
                      for row in rests]}


def archive_evidence(performance, source_path):
    return {'version': 1, 'policy': POLICY, 'timeDomain': 'score_seconds',
            'sourceSha256': hashlib.sha256(source_path.read_bytes()).hexdigest(),
            'continuations': deepcopy(performance['restTieEvidence'])}


def report_findings(performance, report):
    from .compatibility import add_finding
    for row in performance.get('restTieEvidence', []):
        add_finding(report, feature='note.rest_tie', category='source_interpretation', impact='source_retained',
                    message='The tied continuation holds its preceding note through written rests, '
                            'matching source playback. The original rests and stored fret remain in the source.',
                    location=row['location'] + f"@visit{row['occurrence']}",
                    value={k: row[k] for k in ('authored', 'used', 'rule', 'originSourceId', 'bridgesRest')},
                    trackId=row['trackId'])
