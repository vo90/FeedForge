"""Songsterr's pitched trill sequence, retained separately from written notation.

The interval, short-note threshold and final remainder follow the public
player's Fs/Ar stages. Use its per-part adaptive clock, not milliseconds.
"""
from copy import deepcopy
from fractions import Fraction as F
from math import gcd

from .model import ScoreImportError, integer, rational

POLICY = 'songsterr-trill-hopo-v1'


def fail(message):
    error = ScoreImportError('Songsterr trill: ' + message)
    error.source_feature = 'note.trill'
    raise error


def resolution(measures):
    factors, swing, dead, gesture, tremolo = [], False, False, False, False
    for measure in measures:
        swing |= measure.get('tripletFeel') not in (None, 'off')
        for voice in measure.get('voices', []):
            if voice.get('rest'):
                continue
            for beat in voice.get('beats', []):
                tuplet = beat.get('tuplet')
                if isinstance(tuplet, int) and tuplet > 0 and tuplet not in factors:
                    factors.append(tuplet)
                duration = beat.get('duration')
                if not isinstance(duration, list) or len(duration) != 2:
                    continue
                denominator = integer(duration[1], 'duration denominator')
                if denominator <= 0 or denominator & (denominator - 1) or denominator > 32768:
                    continue
                if denominator not in factors:
                    factors.append(denominator)
                for note in beat.get('notes', []):
                    dead |= bool(note.get('dead'))
                    gesture |= bool(note.get('bend') or note.get('slide'))
                    tremolo |= bool(note.get('tremolo'))
    factors += ([24, 48] if swing else []) + ([128] if dead else [])
    factors += ([60] if gesture else []) + ([8, 16, 32, 64] if tremolo else [])
    value = max([32] + [n * 4 for n in factors])
    for factor in factors:
        candidate = value * factor // gcd(value, factor)
        if 0 < candidate < 32768:
            value = candidate
    while value < 10000:
        value *= 2
    return min(value, 32767)


def read(raw, beat, tpqn):
    trill = raw.get('trill')
    if trill is None:
        return None
    if not isinstance(trill, dict) or set(trill) != {'auxiliaryFret', 'speed'}:
        fail('expected auxiliaryFret and speed, with no unknown fields.')
    auxiliary = integer(trill['auxiliaryFret'], 'trill auxiliary fret')
    speed = rational(trill['speed'], 'trill speed')
    if not 0 <= auxiliary <= 24 or not 0 <= raw.get('fret', -1) <= 24 or auxiliary == raw.get('fret'):
        fail('distinct pitched frets from 0 to 24 are required.')
    if not 0 < speed <= 960:
        fail('speed must be greater than zero and at most 960.')
    # The duration cap uses the written beat, before swing or tie folding.
    cap = min(960, int(1920 * rational(beat.get('duration', [1, 4]), 'trill beat duration')))
    ticks = int(tpqn * min(speed, cap) / 480)
    if ticks < 1:
        fail('the speed is below the source playback clock resolution.')
    return {'auxiliaryFret': auxiliary, 'speed': str(speed), 'tpqn': tpqn, 'ticks': ticks}


def check_segment(note, state):
    trill = state.get('trill') or note.trill
    if not trill:
        return
    if note.tie and note.trill:
        fail('a trill marking on a tied continuation needs separate interpretation.')
    allowed = {'pm', 'ghost', 'ac', 'vb', 'pkd', '__hopo_origin', '__wide_vibrato'}
    if (set(note.effects) - allowed or note.bends or note.slide or note.slide_in
            or note.whammy or note.pick_scrape or note.attack_offset):
        fail('this combination with another gesture needs additional verification.')


def expand(rendered, states, links, track_id, at, evidence, authored_groups):
    tails, records = {}, []
    original_count = len(rendered)
    for output in list(rendered):
        state = states[id(output)]
        spec = state.get('trill')
        if not spec:
            continue
        start, end = state['start'], state['end']
        step = F(spec['ticks'], spec['tpqn'])
        duration = end - start
        count = 1 if duration < step * F(3, 2) else max(2, int((duration + F(1, spec['tpqn'])) / step))
        if len(rendered) + count - 1 > 500000:
            fail('expansion exceeds the performed-note limit.')
        original, sequence = deepcopy(output), []
        for ordinal in range(count):
            left = start + ordinal * step
            right = end if ordinal == count - 1 else start + (ordinal + 1) * step
            if left >= right:
                fail('the source clock produced an empty final event.')
            fret = original['f'] if ordinal % 2 == 0 else spec['auxiliaryFret']
            if ordinal == 0:
                row = output
            else:
                row = {k: deepcopy(v) for k, v in original.items() if k in {'s', 'source_ids', 'pm', 'ghost', 'vb'}}
                row['ho' if fret > sequence[-1]['f'] else 'po'] = True
                rendered.append(row)
            row.update(t=at(left), sus=at(right) - at(left), f=fret)
            row.pop('ln', None)
            if ordinal < count - 1 or original.get('ln'):
                row['ln'] = True
            sequence.append(row)
        tails[id(output)] = sequence[-1]['f']
        records.append((state, spec, original, sequence, step))
    sequences = {id(rows[0]): rows for _, _, _, rows, _ in records}
    for key, group in list(authored_groups.items()):
        if not any(id(row) in sequences for row in group):
            continue
        del authored_groups[key]
        for original in group:
            for row in sequences.get(id(original), [original]):
                authored_groups.setdefault(((key[0], row['t']), key[1]), []).append(row)
    for previous, following in links:
        if id(previous) not in tails:
            continue
        previous_fret = tails[id(previous)]
        if following['f'] == previous_fret:
            fail('the authored outgoing legato reaches the same final trill pitch; its reattack is ambiguous.')
        following.pop('ho', None); following.pop('po', None)
        following['ho' if following['f'] > previous_fret else 'po'] = True
    for state, spec, original, sequence, step in records:
        identity = f"{original['source_ids'][0]}@{state['occurrence']}:trill"
        evidence.append({'id': identity, 'trackId': track_id, 'occurrence': state['occurrence'],
            'sourceIds': original['source_ids'], 'string': original['s'], 'mainFret': original['f'],
            **spec, 'startQuarter': str(state['start']), 'endQuarter': str(state['end']),
            'events': [{'id': f'{identity}:{i}', 'ordinal': i, 'fret': row['f'],
                        'startQuarter': str(state['start'] + i * step),
                        'endQuarter': str(state['end'] if i == len(sequence)-1 else state['start'] + (i+1)*step),
                        'articulation': 'ho' if row.get('ho') else 'po' if row.get('po') else 'initial',
                        'linked': bool(row.get('ln'))} for i, row in enumerate(sequence)]})
    return len(rendered) - original_count


def archive_evidence(performance, source_path):
    import hashlib
    return {'version': 1, 'policy': POLICY, 'timeDomain': 'quarter_notes',
            'sourceSha256': hashlib.sha256(source_path.read_bytes()).hexdigest(),
            'trills': performance['trillEvidence']}
