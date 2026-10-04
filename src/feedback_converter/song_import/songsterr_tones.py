"""Authored track sounds, before repeats or recording-time preparation.

The public player's Ti stage scales static positions to the actual bar length;
setupSoundAutomations adds one tick at an all-tied boundary. These are track
instructions, not note/section heuristics or descriptions of amplifier rigs.
"""
from fractions import Fraction as F
import hashlib
import json

from .model import ScoreImportError, integer, rational
from .songsterr_trills import resolution

POLICY = 'songsterr-tone-timeline-v1'
LABELS = {24: 'Nylon Guitar', 25: 'Steel Guitar', 26: 'Jazz Guitar',
          27: 'Clean Guitar', 28: 'Muted Guitar', 29: 'Overdriven Guitar',
          30: 'Distortion Guitar', 31: 'Guitar Harmonics', 32: 'Acoustic Bass',
          33: 'Finger Bass', 34: 'Pick Bass', 35: 'Fretless Bass',
          36: 'Slap Bass 1', 37: 'Slap Bass 2', 38: 'Synth Bass 1', 39: 'Synth Bass 2'}


def fail(message):
    error = ScoreImportError('Songsterr tones: ' + message)
    error.source_feature = 'track.sounds'
    raise error


def catalogue(part, meta, index, song_id):
    """Naming/identity only. No timing or playback decisions live here."""
    initial = part.get('instrumentId', meta.get('instrumentId', meta.get('midiProgram')))
    if initial is not None:
        if type(initial) not in (int, float):
            fail('invalid initial instrument program.')
        initial = integer(initial, 'initial sound program')
        if not 0 <= initial <= 127:
            fail('invalid initial instrument program.')
    sounds = part.get('sounds', [])
    if not isinstance(sounds, list) or len(sounds) > 1024:
        fail('invalid sound catalogue.')
    identity = json.dumps([str(song_id), str(meta.get('id', index)), index], separators=(',', ':'))
    scope = hashlib.sha256(identity.encode()).hexdigest()[:16]
    entries = []
    for i, sound in enumerate(sounds):
        if not isinstance(sound, dict) or set(sound) - {'instrumentId', 'label'}:
            fail('unknown sound definition.')
        program = sound.get('instrumentId')
        label = sound.get('label', '')
        if type(program) not in (int, float) or not isinstance(label, str):
            fail('invalid sound program or label.')
        program = integer(program, 'sound program')
        if not 0 <= program <= 127:
            fail('invalid sound program.')
        display = label.strip() or LABELS.get(program, f'GM {program}')
        entries.append({'soundId': i, 'instrumentId': program, 'label': label,
                        'name': f'{display} [st-{scope}-s{i}]'})
    matching = [s for s in entries if s['instrumentId'] == initial]
    if len(matching) == 1:
        base = matching[0]['name']
    else:
        label = LABELS.get(initial, f'GM {initial}' if initial is not None else 'Unspecified')
        base = f'{label} [st-{scope}-initial]'
        entries.insert(0, {'soundId': None, 'instrumentId': initial, 'label': label, 'name': base})
    return {'initialProgram': initial, 'base': base, 'sounds': entries,
            'sourceTrackId': str(meta.get('id', index)), 'sourcePartIndex': index}


def read(part, meta, measures, clocks, index, song_id, inventory, all_measures):
    result = catalogue(part, meta, index, song_id)
    automation = part.get('trackAutomations', {})
    if not isinstance(automation, dict):
        fail('invalid track automation object.')
    inventory.inspect(automation, 'Songsterr track automations', f'$.parts[{index}].trackAutomations',
                      playable={'trackSoundAutomations'}, strict=True)
    raw = automation.get('trackSoundAutomations', [])
    if not isinstance(raw, list) or len(raw) > 100_000:
        fail('invalid sound event list.')
    tpqn = resolution(all_measures)
    names = {s['soundId']: s['name'] for s in result['sounds'] if s['soundId'] is not None}
    events = []
    for i, event in enumerate(raw):
        if not isinstance(event, dict) or set(event) != {'measure', 'position', 'soundId'}:
            fail('a sound event requires measure, position and soundId only.')
        if (type(event['measure']) not in (int, float) or type(event['soundId']) not in (int, float)
                or type(event['position']) not in (int, float)):
            fail('sound coordinates and references must be numeric source values.')
        bi = integer(event['measure'], 'sound measure')
        pos = rational(event['position'], 'sound position')
        sid = integer(event['soundId'], 'sound index')
        if not 0 <= bi < len(measures) or sid not in names:
            fail('a sound event references a missing measure or sound.')
        bar = measures[bi]
        nominal = F(4 * bar.numerator, bar.denominator)
        if not 0 <= pos <= nominal * 960:
            fail('sound position lies outside its written measure.')
        tick = (bar.length * tpqn * pos / (nominal * 960)) // 1
        # mr sums the duration field, NOT swung durationInTicks/startTick.
        # Match its floating arithmetic order; strums do not move this boundary.
        ties = []
        for vi, voice in enumerate(part['measures'][bi]['voices']):
            if voice.get('rest'):
                continue
            cursor = 0.
            for beat, (start, _, _) in zip(voice['beats'], clocks[bi][vi]):
                if start >= bar.length:
                    continue
                if cursor == tick and not beat.get('rest'):
                    ties.extend(n.get('tie') is True for n in beat['notes'] if not n.get('rest'))
                duration = rational(beat['duration'])
                cursor += 4 * tpqn / duration.denominator * duration.numerator
        guard = int(bool(ties) and all(ties))
        events.append({'sourceIndex': i, 'measure': bi, 'position': float(pos),
                       'soundId': sid, 'name': names[sid], 'q': F(tick + guard, tpqn),
                       'tieGuardTicks': guard})
    result.update(events=events, tpqn=tpqn)
    return result


def perform(source, visits, at, end):
    if source is None:
        return None
    by_measure = {}
    for event in source['events']:
        by_measure.setdefault(event['measure'], []).append(event)
    events = []
    for occurrence, (measure, start) in enumerate(visits, 1):
        for event in by_measure.get(measure, []):
            q = start + event['q']
            events.append({k: v for k, v in event.items() if k != 'q'} |
                          {'occurrence': occurrence, 'quarter': [q.numerator, q.denominator], 'time': at(q)})
            if len(events) > 500_000:
                fail('performed sound event limit exceeded.')
    # Stable order gives the final source instruction precedence at equal times.
    events.sort(key=lambda e: e['time'])
    return {k: v for k, v in source.items() if k != 'events'} | {'events': events, 'scoreEnd': at(end)}
