"""Recording-domain tone schedules. No rig or synthesizer realization."""
from bisect import bisect_right
from copy import deepcopy
import hashlib
import json

from .alignment import map_time
from .audio import ImportFailure
from .songsterr_tones import POLICY


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def clock_parameters(alignment):
    """Bind the applied map, excluding acoustic assessments and other receipts."""
    if alignment.get('mapping') == 'piecewise-linear':
        return {'mapping': 'piecewise-linear',
                'anchors': [{'score': a['score'], 'audio': a['audio']} for a in alignment['anchors']],
                'openingStrum': alignment.get('provenance', {}).get('openingStrum')}
    return {'offset': alignment['offset'], 'scale': alignment['scale']}


def export(timeline, alignment, duration):
    base = timeline['base']
    changes, ledger = [], []
    previous_time = None
    for event in timeline['events']:
        if event['time'] >= timeline['scoreEnd']:
            ledger.append({**event, 'disposition': 'after_score'})
            continue
        time = map_time(alignment, event['time'], allow_negative=True)
        row = {**event, 'audioTime': time}
        if time <= 0:
            base = event['name']
            row['disposition'] = 'initial_state'
        elif time >= duration:
            row['disposition'] = 'after_recording'
        elif changes and changes[-1]['t'] == time:
            if previous_time != event['time'] and changes[-1]['name'] != event['name']:
                raise ImportFailure('tone_timing_failed', 'Distinct sound changes collapse at the export timestamp precision.')
            changes[-1]['name'] = event['name']
            row['disposition'] = 'same_time_state'
        else:
            changes.append({'t': time, 'name': event['name']})
            row['disposition'] = 'transition'
        ledger.append(row)
        previous_time = event['time']
    current, compact = base, []
    for change in changes:
        if change['name'] != current:
            compact.append(change)
            current = change['name']
    chart = {'base': base, 'changes': compact}
    proof = {k: deepcopy(v) for k, v in timeline.items() if k != 'events'}
    proof.update(events=ledger, tones=deepcopy(chart))
    return chart, proof


class State:
    def __init__(self, tones):
        self.times = [e['t'] for e in tones['changes']]
        self.names = [tones['base']] + [e['name'] for e in tones['changes']]

    def at(self, time):
        return self.names[bisect_right(self.times, time)]


def hybrid(plan, originals, duration):
    """Switch owners over validated passage spans, checking selected sustains.

    A passage owns its declared region AND complete selected gestures. A main
    sustain overlapping a donor with a different program cannot be represented
    by one active rig and must not be certified as a faithful tone schedule.
    """
    main = plan['mainTrackId']
    if 'tones' not in originals[main]['chart']:
        return None
    states = {tid: State(row['chart']['tones']) for tid, row in originals.items()}
    programs = {s['name']: s['instrumentId'] for row in originals.values()
                for s in row['toneProof']['sounds']}
    spans = []
    for p in plan['passages']:
        lo = max(0, min(p['recordingStart'], p.get('recordingOwnedStart', p['recordingStart'])))
        hi = min(duration, max(p['recordingEnd'], p.get('recordingOwnedEnd', p['recordingEnd'])))
        if lo < hi:
            spans.append((lo, hi, p['trackId']))
    spans.sort()
    if any(a[1] > b[0] for a, b in zip(spans, spans[1:])):
        raise ImportFailure('tone_conflict', 'Overlapping Hybrid sound owners require review.')
    boundaries = {0., duration}
    boundaries.update(t for lo, hi, _ in spans for t in (lo, hi))
    boundaries.update(t for state in states.values() for t in state.times if 0 <= t < duration)
    def owner(time):
        return next((tid for lo, hi, tid in spans if lo <= time < hi), main)
    schedule = []
    for time in sorted(boundaries):
        if time >= duration:
            continue
        tid = owner(time)
        name = states[tid].at(time)
        if not schedule or name != schedule[-1]['name']:
            schedule.append({'t': time, 'name': name})
    result = {'base': schedule[0]['name'], 'changes': schedule[1:]}
    actual = State(result)
    selected = []
    if 'mainEvents' in plan:
        selected.extend((main, originals[main]['chart'][r['kind']][r['index']]) for r in plan['mainEvents'])
    else:
        selected.extend((main, e) for k in ('notes', 'chords') for e in originals[main]['chart'][k])
    selected.extend((p['trackId'], originals[p['trackId']]['chart'][r['kind']][r['index']])
                    for p in plan['passages'] for r in p['events'])
    edges = sorted(boundaries)
    for tid, event in selected:
        for note in event.get('notes', [event]):
            start = note.get('t', event['t'])
            end = min(duration, start + note.get('sus', 0))
            probes = [start] + edges[bisect_right(edges, start):bisect_right(edges, end - 0.0000011)]
            for time in probes:
                expected, got = states[tid].at(time), actual.at(time)
                # Unknown sounds are only equivalent when their identity agrees.
                if expected != got and (programs[expected] is None or programs[expected] != programs[got]):
                    raise ImportFailure('tone_conflict', f'Hybrid requires incompatible sounds at {time:.6f}s; '
                                        'export original arrangements with Hybrid disabled or revise the passage selection.')
    return result


def receipt(rows, source_path, audio_hash, alignment, duration):
    return {'version': 1, 'policy': POLICY, 'timeDomain': 'recording_seconds',
            'sourceSha256': hashlib.sha256(source_path.read_bytes()).hexdigest(),
            'audioSha256': audio_hash, 'alignmentSha256': digest(clock_parameters(alignment)),
            'duration': duration, 'tracks': rows}
