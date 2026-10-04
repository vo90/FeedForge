"""Authored lyric interpretation, independent of playable instrument selection.

The public Songsterr player assigns its primary lyric row to voice zero, skips
rests/grace notes, and consumes consecutive spaces as empty syllable slots. A
tie does not consume the next syllable. See docs/songsterr-lyrics.md and the
pinned public-player comparison fixtures before changing these rules.
"""
from fractions import Fraction
import re

from .model import ScoreImportError, integer, rational
from .songsterr_timing import part_timing

POLICY = 'songsterr-authored-lyrics-v1'
MAX_TEXT = 20_000
MAX_EVENTS = 100_000
_SEPARATORS = re.compile(r'(\[.*?\]|\s?-\s?)|\r\n|\n|\s')
_VOCAL = re.compile(r'vocal|\bvox\b|\bvoice\b', re.I)


def primary_row(part):
    rows = part.get('newLyrics') or []
    if not isinstance(rows, list):
        raise ValueError('Invalid lyric rows.')
    index = 0 if len(rows) <= 5 else 5
    row = rows[index] if len(rows) > index else {}
    if not isinstance(row, dict) or not isinstance(row.get('text', ''), str):
        raise ValueError('Invalid lyric text.')
    return row


def tokens(text):
    """Keep separators: collapsing whitespace moves lyrics onto different notes."""
    if len(text.encode('utf-16-le')) // 2 > MAX_TEXT or len(re.findall(r'\r\n|\r|\n', text)) >= 500:
        raise ValueError('Lyrics exceed the public player text limit; source retained.')
    text = text.replace('\t', ' ').replace('-—–', '-')
    original_length = len(text)
    text += '\n'
    result, cursor, comment = [], 0, False
    for match in _SEPARATORS.finditer(text):
        delimiter = match.group()
        is_comment = delimiter.startswith('[')
        if match.start() > cursor:
            value = re.sub(r'^.*\]', '', text[cursor:match.start()])
            if not is_comment and '-' in delimiter:
                value += '-'
                delimiter = re.sub(r'\s?-\s?', '', delimiter)
            result.append({'kind': 'lyric', 'text': value.replace('(', '').replace(')', ''),
                           'position': cursor})
            comment = False
        cursor = match.end()
        if is_comment:
            if result and result[-1]['kind'] == 'space':
                result[-1]['comment'] = True
            comment = True
        else:
            result.append({'kind': 'space', 'text': delimiter.replace('\r', ''),
                           'position': match.start(), 'comment': comment, 'terminal': match.start() >= original_length})
            comment = False
    return result


def assign(part, text, offset):
    """Return one textual slot per *source* beat; no seconds or repeats yet."""
    stream = tokens(text)
    cursor, previous = 0, None
    rows = []
    for measure_index, measure in enumerate(part['measures']):
        beats = measure['voices'][0]['beats']
        output = []
        for beat in beats:
            value, position, consumed_skip, line_end = '', None, False, False
            tied = all(n.get('tie') for n in beat.get('notes', []))
            if measure_index >= offset - 1 and not beat.get('rest') and not beat.get('graceNote'):
                # Songsterr's loop always inspects one token, even at the end.
                while cursor < len(stream):
                    token = stream[cursor]
                    position = token['position']
                    if token['kind'] == 'lyric':
                        if not tied:
                            value = token['text'].replace('+', ' ')
                            cursor += 1
                            previous = token
                            line_end = cursor < len(stream) and stream[cursor]['text'] == '\n' and not stream[cursor].get('terminal')
                        break
                    cursor += 1
                    doubled = previous and previous['kind'] == 'space' and previous['text'] != '\n'
                    previous = token
                    if doubled:
                        consumed_skip = True
                        break
                    if cursor >= len(stream) - 1:
                        break
            output.append({'text': value, 'position': position, 'tie': tied,
                           'extension': bool(value) and not value.strip('_'),
                           'skip': consumed_skip, 'lineEnd': line_end})
        rows.append(output)
    remaining = sum(t['kind'] == 'lyric' and bool(t['text'].strip('_')) for t in stream[cursor:])
    return rows, remaining


def select(document):
    """One authored stream; never concatenate lead and backing vocal parts."""
    candidates, invalid = [], []
    for index, (meta, part) in enumerate(zip(document['tracks'], document['parts'])):
        try:
            row = primary_row(part)
        except ValueError:
            invalid.append(index)
            continue
        modern = bool(row.get('text', '').strip())
        legacy = part.get('withLyrics') is True and not modern and bool(document.get('legacyLyrics'))
        if not modern and not legacy:
            continue
        name = str(meta.get('name') or part.get('name') or f'Track {index + 1}')
        candidates.append({'index': index, 'name': name, 'kind': 'legacy' if legacy else 'modern',
                           'designated': part.get('withLyrics') is True,
                           'vocal': meta.get('isVocalTrack') is True or bool(_VOCAL.search(name))})
    designated = [c for c in candidates if c['designated']]
    vocals = [c for c in candidates if c['vocal']]
    pool = designated or vocals or candidates
    if not pool:
        acquisition = document.get('lyricsAcquisition') or {}
        unavailable = isinstance(acquisition, dict) and acquisition.get('status') == 'unavailable'
        return None, {'status': 'unavailable' if unavailable else 'unsupported' if invalid else 'absent',
                      'reason': 'legacy_download_unavailable' if unavailable else 'invalid_lyric_rows' if invalid else 'no_authored_lyrics',
                      'candidates': candidates}
    if not designated and not vocals and len(pool) > 1:
        return None, {'status': 'unsupported', 'reason': 'ambiguous_lyric_tracks', 'candidates': candidates}
    chosen = pool[0]
    return chosen, {'status': 'ready', 'selection': 'designated_track' if designated else 'first_vocal_track' if vocals else 'only_lyric_track',
                    'trackIndex': chosen['index'], 'trackName': chosen['name'], 'sourceKind': chosen['kind'],
                    'candidates': candidates}


def legacy_assign(part, measures):
    """Legacy sidecars have per-measure beats; require matching written positions."""
    if not isinstance(measures, list) or len(measures) > len(part['measures']):
        raise ValueError('Invalid legacy lyric measures.')
    output = []
    for bi, bar in enumerate(part['measures']):
        native = measures[bi].get('beats', []) if bi < len(measures) else []
        if not isinstance(native, list):
            raise ValueError('Invalid legacy lyric beats.')
        positions, q = {}, Fraction(0)
        for row in native:
            duration = rational(row.get('duration'), 'legacy lyric duration') * 4
            if duration <= 0:
                raise ValueError('Invalid legacy lyric duration.')
            syllables = row.get('lyrics') or []
            if not isinstance(syllables, list) or len(syllables) > 1:
                raise ValueError('Unsupported overlapping legacy syllables.')
            text = syllables[0].get('text', '') if syllables else ''
            if not isinstance(text, str):
                raise ValueError('Invalid legacy lyric text.')
            if text.strip():
                positions[q] = text
            q += duration
        q, slots = Fraction(0), []
        for beat in bar['voices'][0]['beats']:
            text = positions.pop(q, '') if not beat.get('graceNote') else ''
            if text and beat.get('rest'):
                raise ValueError('Legacy lyric falls on a rest.')
            slots.append({'text': text, 'position': None,
                          'tie': bool(beat.get('notes')) and all(n.get('tie') for n in beat['notes']),
                          'extension': bool(text) and not text.strip('_'), 'skip': False})
            if not beat.get('graceNote'):
                q += rational(beat['duration']) * 4
        if positions:
            raise ValueError('Legacy lyrics do not match the vocal beat positions.')
        output.append(slots)
    return output, 0


def written(document, lengths):
    try:
        chosen, report = select(document)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        chosen, report = None, {'status': 'unsupported', 'reason': str(exc), 'candidates': []}
    report.update(policy=POLICY, events=[], warnings=[])
    if chosen is None:
        return report
    try:
        part = document['parts'][chosen['index']]
        if len(part['measures']) != len(lengths):
            raise ValueError('The lyric track has inconsistent measures.')
        clocks = part_timing(part['measures'], lengths)
        if chosen['kind'] == 'legacy':
            assigned, remaining = legacy_assign(part, document['legacyLyrics'])
        else:
            row = primary_row(part)
            offset = integer(row.get('offset', 1), 'lyric start measure')
            if not 1 <= offset <= len(lengths):
                raise ValueError('Invalid lyric start measure.')
            assigned, remaining = assign(part, row['text'], offset)
        if remaining:
            report['warnings'].append(f'{remaining} lyric syllables follow the last available vocal note; retained in source only.')
        for bi, slots in enumerate(assigned):
            for beat_index, slot in enumerate(slots):
                q, length, _ = clocks[bi][0][beat_index]
                beat = part['measures'][bi]['voices'][0]['beats'][beat_index]
                text = slot['text'].strip()
                if len(text) > 200 or any(ord(c) < 32 for c in text if c not in '\n\r\t'):
                    raise ValueError('Unsupported lyric text/control characters.')
                if q < 0 or q + length > lengths[bi]:
                    raise ValueError('Lyric beat crosses a written measure boundary.')
                # Slots remain explicit so a repeat jump can never extend a
                # syllable through a rest or borrow one from a different verse.
                report['events'].append({'measure': bi, 'beat': beat_index, 'q': q, 'length': length,
                                         'text': text + ('\n' if text and slot.get('lineEnd') else ''),
                                         'tie': slot['tie'], 'extension': slot['extension'],
                                         'rest': bool(beat.get('rest')), 'grace': bool(beat.get('graceNote'))})
        if len(report['events']) > MAX_EVENTS:
            raise ValueError('Too many lyric beats.')
        report['unassignedSyllables'] = remaining
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, ScoreImportError) as exc:
        report.update(status='unsupported', reason=str(exc), events=[])
    return report


def perform(document, lengths, visits, at):
    result = written(document, lengths)
    slots = result.pop('events')
    by_bar = {}
    for slot in slots:
        by_bar.setdefault(slot['measure'], []).append(slot)
    events, previous_end, previous_bar = [], None, None
    for occurrence, (bar, origin) in enumerate(visits):
        # Continuations cannot cross written navigation jumps.
        if previous_bar is not None and bar != previous_bar + 1:
            previous_end = None
        for slot in by_bar.get(bar, []):
            start, end = origin + slot['q'], origin + slot['q'] + slot['length']
            text = slot['text']
            continuation = slot['extension'] or slot['tie'] and not text
            if slot['rest']:
                previous_end = None
                continue
            if slot['grace']:
                continue
            if continuation and events and previous_end == start:
                events[-1]['end'] = at(end)
                previous_end = end
            elif text and not slot['extension']:
                events.append({'time': at(start), 'end': at(end), 'text': text,
                               'measure': bar, 'beat': slot['beat'], 'occurrence': occurrence + 1})
                previous_end = end
            else:
                previous_end = None
            if len(events) > MAX_EVENTS:
                result.update(status='unsupported', reason='Too many repeated lyric events.')
                events = []
                break
        if result['status'] == 'unsupported':
            break
        previous_bar = bar
    if result['status'] == 'ready' and not events:
        result.update(status='unsupported', reason='No lyric text maps to vocal notes.')
    result['events'] = events
    return result
