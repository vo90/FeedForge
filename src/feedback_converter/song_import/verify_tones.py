"""Independent raw-source sound clock and archive checks.

Only catalogue naming is shared. This module does not call the tone parser,
performance renderer, recording mapper, exporter or Hybrid tone composer.
"""
from fractions import Fraction as F
from math import lcm
import hashlib
import json

from .verify_source import fraction, integer, unsupported
from .verify_timeline import Clock, RecordingMap, visits


def read(raw, meta, bars, clocks, index, song_id, all_measures):
    from .songsterr_tones import catalogue
    info = catalogue(raw, meta, index, song_id)
    automation = raw.get('trackAutomations', {})
    where = f'parts/{index}/trackAutomations'
    if not isinstance(automation, dict) or any(v not in (None, False, '', [], {})
            for k, v in automation.items() if k != 'trackSoundAutomations'):
        unsupported(where, 'Unknown track sound automation.')
    changes = automation.get('trackSoundAutomations', [])
    if not isinstance(changes, list) or len(changes) > 100_000:
        unsupported(where, 'Invalid sound changes.')
    divisors, flags = [], set()
    for bar in all_measures:
        if bar.get('tripletFeel') not in (None, 'off'): flags.add('swing')
        for voice in bar['voices']:
            if voice.get('rest'): continue
            for beat in voice['beats']:
                tuplet = beat.get('tuplet')
                if type(tuplet) is int and tuplet > 0 and tuplet not in divisors: divisors.append(tuplet)
                d = beat['duration']
                if not isinstance(d, list) or len(d) != 2: continue
                den = integer(d[1])
                if den not in [2 ** i for i in range(16)]: continue
                if den not in divisors: divisors.append(den)
                for n in beat['notes']:
                    if n.get('dead'): flags.add('dead')
                    if n.get('slide') or n.get('bend'): flags.add('pitch')
                    if n.get('tremolo'): flags.add('tremolo')
    for flag, values in [('swing', [24, 48]), ('dead', [128]), ('pitch', [60]), ('tremolo', [8, 16, 32, 64])]:
        if flag in flags: divisors.extend(values)
    tpqn = max(32, 4 * max(divisors, default=0))
    for d in divisors:
        merged = lcm(tpqn, d)
        if merged < 32768: tpqn = merged
    while tpqn < 10000: tpqn *= 2
    tpqn = min(tpqn, 32767)
    events = []
    names = {s['soundId']: s['name'] for s in info['sounds'] if s['soundId'] is not None}
    for i, event in enumerate(changes):
        if not isinstance(event, dict) or sorted(event) != ['measure', 'position', 'soundId']:
            unsupported(where, 'Unverified sound event fields.')
        if (type(event['measure']) not in (int, float) or type(event['soundId']) not in (int, float)
                or type(event['position']) not in (int, float)):
            unsupported(where, 'Non-numeric source sound coordinates.')
        bar_index, sound_id = integer(event['measure']), integer(event['soundId'])
        if not 0 <= bar_index < len(bars) or sound_id not in names:
            unsupported(where, 'Unknown sound or measure.')
        pos, bar = fraction(event['position']), bars[bar_index]
        nominal = F(4 * bar.signature[0], bar.signature[1])
        if not 0 <= pos <= nominal * 960: unsupported(where, 'Invalid sound position.')
        tick_position = bar.length * pos * tpqn / (nominal * 960)
        ticks = tick_position.numerator // tick_position.denominator
        tie_flags = []
        for vi, voice in enumerate(raw['measures'][bar_index]['voices']):
            if voice.get('rest'): continue
            elapsed = 0.
            for j, beat in enumerate(voice['beats']):
                if clocks[bar_index][vi][j][0] >= bar.length: continue
                if elapsed == ticks and not beat.get('rest'):
                    tie_flags.extend(n.get('tie') is True for n in beat['notes'] if not n.get('rest'))
                length = fraction(beat['duration'])
                elapsed += 4 * tpqn / length.denominator * length.numerator
        guard = 1 if tie_flags and False not in tie_flags else 0
        events.append({'sourceIndex': i, 'measure': bar_index, 'position': float(pos),
                       'soundId': sound_id, 'name': names[sound_id], 'q': F(ticks + guard, tpqn),
                       'tieGuardTicks': guard})
    return {**info, 'tpqn': tpqn, 'events': events}


def expected(tone_source, source, alignment, duration):
    order = visits(source)
    clock, recording = Clock(source, order), RecordingMap(alignment)
    by_measure = {}
    for event in tone_source['events']:
        by_measure.setdefault(event['measure'], []).append(event)
    events = []
    for occurrence, measure in enumerate(order, 1):
        for event in by_measure.get(measure, []):
            q = clock.measure_starts[occurrence - 1] + event['q']
            seconds = clock.at(q)
            events.append(({k: v for k, v in event.items() if k != 'q'} |
                           {'occurrence': occurrence, 'quarter': [q.numerator, q.denominator],
                            'time': float(seconds)}, seconds))
            if len(events) > 500_000: unsupported('tones', 'Too many performed sound events.')
    events.sort(key=lambda row: row[1])
    base, points, ledger = tone_source['base'], {}, []
    for row, seconds in events:
        if seconds >= clock.at(clock.quarters):
            ledger.append({**row, 'disposition': 'after_score'})
            continue
        time = recording.at(seconds)
        if time <= 0:
            base, disposition = row['name'], 'initial_state'
        elif time >= duration:
            disposition = 'after_recording'
        else:
            disposition = 'same_time_state' if time in points else 'transition'
            if time in points and points[time][1] != seconds and points[time][0] != row['name']:
                unsupported('tones', 'Distinct sound states collide at the export precision.')
            points[time] = row['name'], seconds
        ledger.append({**row, 'audioTime': time, 'disposition': disposition})
    previous, changes = base, []
    for time, (name, _) in sorted(points.items()):
        if name != previous: changes.append({'t': time, 'name': name})
        previous = name
    tones = {'base': base, 'changes': changes}
    proof = {k: v for k, v in tone_source.items() if k != 'events'} | {
        'events': ledger, 'tones': tones, 'scoreEnd': float(clock.at(clock.quarters))}
    return tones, proof


def compare(wanted, actual, check, path):
    if isinstance(wanted, dict) and isinstance(actual, dict):
        check.equal('tone_structure', path, sorted(wanted), sorted(actual))
        for key in wanted:
            compare(wanted[key], actual.get(key), check, path + '/' + key)
    elif isinstance(wanted, list) and isinstance(actual, list):
        check.equal('tone_structure', path + '/length', len(wanted), len(actual))
        for i, (a, b) in enumerate(zip(wanted, actual)):
            compare(a, b, check, f'{path}/{i}')
    elif path.rsplit('/', 1)[-1] in {'time', 'audioTime', 't', 'duration', 'scoreEnd'}:
        check.near('tone_time', path, wanted, actual)
    else:
        check.equal('tone_state', path, wanted, actual)


def verify(z, recipe, source, selected, alignment, duration, source_hash, manifest, check):
    from .songsterr_tones import POLICY
    check.equal('tone_contract', 'song_import/toneTimelinePolicy', POLICY, recipe.get('toneTimelinePolicy'))
    check.equal('tone_contract', 'song_import/toneTimelineFile', 'import/tone-timeline.json', recipe.get('toneTimelineFile'))
    if recipe.get('preservationContract', 0) < 73:
        check.fail('tone_contract', 'song_import', 'Tone timelines require preservation contract 73.')
    proof = json.loads(z.read('import/tone-timeline.json'))
    parameters = ({'mapping': 'piecewise-linear',
                   'anchors': [dict(score=a['score'], audio=a['audio']) for a in alignment['anchors']],
                   'openingStrum': alignment.get('provenance', {}).get('openingStrum')}
                  if alignment.get('mapping') == 'piecewise-linear'
                  else {'offset': alignment['offset'], 'scale': alignment['scale']})
    digest = hashlib.sha256(json.dumps(parameters, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
    full = next(s for s in manifest['stems'] if s['id'] == 'full')
    audio_hash = hashlib.sha256(z.read(full['file'])).hexdigest()
    wanted = {'version': 1, 'policy': POLICY, 'timeDomain': 'recording_seconds',
              'sourceSha256': source_hash, 'audioSha256': audio_hash, 'alignmentSha256': digest,
              'duration': duration, 'tracks': []}
    for part, arr, chart in selected:
        tones, detail = expected(part.tone_source, source, alignment, duration)
        compare(tones, chart.get('tones'), check, arr['file'] + '/tones')
        wanted['tracks'].append({'trackId': part.id, 'arrangementId': arr['id'], **detail})
    compare(wanted, proof, check, 'import/tone-timeline')


def hybrid(receipt, parts, source, alignment, duration, charts, check):
    schedules = {tid: expected(part['source'].tone_source, source, alignment, duration)[0]
                 for tid, part in parts.items()}
    programs = {s['name']: s['instrumentId'] for part in parts.values() for s in part['source'].tone_source['sounds']}
    main = receipt['mainTrackId']
    clock = Clock(source, visits(source))
    recording = RecordingMap(alignment)
    def at(q): return recording.at(clock.at(fraction(q)))
    intervals = [(max(0, at(min(p['start'], p.get('ownedStart', p['start'])))),
                  min(duration, at(max(p['end'], p.get('ownedEnd', p['end'])))), p['trackId'])
                 for p in receipt['passages']]
    intervals = sorted((a, b, tid) for a, b, tid in intervals if a < b)
    if any(a[1] > b[0] + .0000011 for a, b in zip(intervals, intervals[1:])):
        check.fail('tone_conflict', 'hybrid', 'Overlapping source sound ownership.')
    def state(tid, t):
        return next((c['name'] for c in reversed(schedules[tid]['changes']) if c['t'] <= t), schedules[tid]['base'])
    times = {0.} | {t for a, b, _ in intervals for t in (a, b)}
    times.update(c['t'] for s in schedules.values() for c in s['changes'])
    points = []
    for t in sorted(times):
        if t >= duration: continue
        owners = [tid for a, b, tid in intervals if a <= t < b]
        name = state(owners[0] if owners else main, t)
        if not points or points[-1]['name'] != name: points.append({'t': t, 'name': name})
    # Independently selected note references have already been checked by the
    # caller. Test sound compatibility throughout their retained sustains.
    refs = ([(main, r) for r in receipt['mainEvents']] if 'mainEvents' in receipt else
            [(main, {'kind': k, 'index': i}) for k in ('notes', 'chords') for i in range(len(charts[main][k]))])
    refs += [(p['trackId'], r) for p in receipt['passages'] for r in p['events']]
    for tid, ref in refs:
        event = charts[tid][ref['kind']][ref['index']]
        for note in event.get('notes', [event]):
            start = note.get('t', event['t'])
            end = min(duration, start + note.get('sus', 0))
            for time in {start} | {t for t in times if start < t < end - .0000011}:
                actual = next(p['name'] for p in reversed(points) if p['t'] <= time)
                wanted = state(tid, time)
                if wanted != actual and (programs[wanted] is None or programs[wanted] != programs[actual]):
                    check.fail('tone_conflict', 'hybrid', 'Selected material requires incompatible simultaneous sounds.')
    return {'base': points[0]['name'], 'changes': points[1:]}
