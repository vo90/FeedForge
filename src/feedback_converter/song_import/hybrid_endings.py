"""Select complete donor phrases using already accepted terminal sustains.

This grants no new permission to trim audio, notes or techniques. The builder
has finalized the originals first; every source attack must still be present.
Written boundaries remain in the receipt, separately from the playable end.
"""
from copy import deepcopy

from .alignment import map_time
from .terminal_sustains import allowed

TOL = 0.0000011
POLICY = 'accepted-terminal-sustain-v1'


def project(passage, source, original, clock, alignment, duration):
    if not original or not allowed(alignment, duration):
        return passage
    source_end = map_time(alignment, clock.seconds(passage['end']), allow_negative=True)
    if source_end <= duration + TOL:
        return passage
    shortened = False
    for ref in passage['events']:
        key = ref['kind'], ref['index']
        if key not in original['eventIndices']:
            return passage
        authored = source['track'][key[0]][key[1]]
        accepted = original['chart'][key[0]][original['eventIndices'][key]]
        atoms = lambda e: [e] if key[0] == 'notes' else [{'t': e['t'], **n} for n in e['notes']]
        before, after = atoms(authored), atoms(accepted)
        # An endpoint-omitted chord member is not a shortened held tail.
        if len(before) != len(after):
            return passage
        for raw, final in zip(before, after):
            start = map_time(alignment, raw['t'], allow_negative=True)
            end = map_time(alignment, raw['t'] + raw.get('sus', 0), allow_negative=True)
            final_end = final['t'] + final.get('sus', 0)
            if (start >= duration or abs(start - final['t']) > TOL
                    or (raw['s'], raw['f']) != (final['s'], final['f'])
                    or final_end > duration + TOL):
                return passage
            shortened |= end > duration + TOL and final_end < end - TOL
    if not shortened:
        return passage
    lo, hi = passage['start'], passage['end']
    if map_time(alignment, clock.seconds(lo), allow_negative=True) >= duration:
        return passage
    for _ in range(48):
        mid = (lo + hi) / 2
        if map_time(alignment, clock.seconds(mid), allow_negative=True) < duration:
            lo = mid
        else:
            hi = mid
    result = deepcopy(passage)
    result['acceptedEnding'] = {'policy': POLICY, 'sourceEnd': passage['end'],
                                'sourceRecordingEnd': source_end}
    result['end'] = (lo + hi) / 2
    from .hybrid_lead import union
    active = union([(b['start'], b['end']) for b in source['context']['beats'] if not b['rest']] +
                   [(clock.quarter(n['t']), clock.quarter(n['t'] + n.get('sus', 0)))
                    for r in source['rows'] for n in r['notes']])
    result['activeQuarterBeats'] = round(sum(max(0, min(b, result['end']) - max(a, result['start']))
                                                for a, b in active), 8)
    return result
