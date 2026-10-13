"""Independent source expectation for visual ringing; no producer imports."""
from bisect import bisect_right
from collections import defaultdict
from .verify_timeline import Clock, RecordingMap


def attach(source, result, alignment, duration, *, raw=None, rest_bridge_ties=True):
    if source.format != 'songsterr':
        return
    clock, mapping = Clock(source, result['order']), RecordingMap(alignment)
    candidates = defaultdict(list)
    short_origins = set()
    for part in source.parts:
        voices = {b['voice'] for bar in part.beats for b in bar}
        for voice in voices:
            sequence = []
            for occurrence, bar in enumerate(result['order']):
                beats = [b for b in part.beats[bar] if b['voice'] == voice]
                sequence.extend((b, clock.measure_starts[occurrence]) for b in beats)
                if not beats:
                    sequence.append((None, None))
            boundary = None
            for beat, origin in reversed(sequence):
                if beat is None or beat['rest'] or not beat['notes']:
                    boundary = None
                    continue
                own_end = origin + beat['q'] + beat['length']
                if not beat['notation'].get('lr'):
                    boundary = own_end
                    continue
                if boundary is None:
                    boundary = own_end
                for atom in beat['notes']:
                    if atom.staccato:
                        short_origins.add(atom.location)
                    if (atom.bends or atom.slide or atom.slide_in or atom.hopo_origin
                            or atom.effects.get('mt') or atom.trill):
                        continue
                    start = float(clock.at(origin + atom.q + atom.attack_offset))
                    end = float(clock.at(boundary))
                    candidates[atom.location].append((start, end))
    for rows in candidates.values():
        rows.sort()
    # Work in score time to select tied segments; map both endpoints separately.
    if raw is None and (alignment.get('scale', 1) != 1 or alignment.get('offset', 0) or alignment.get('mapping')):
        from .verify_timeline import expected
        raw = expected(source, {'scale': 1, 'offset': 0}, rest_bridge_ties=rest_bridge_ties)
    for pi, part in enumerate(result['parts']):
        reference = raw['parts'][pi]['notes'] if raw else part['notes']
        originals = {(tuple(r['locations']), r['occurrence']): r for r in reference}
        attacks = defaultdict(set)
        for item in part['notes']:
            n = item['note']; attacks[n['s']].add(n['t'])
        attacks = {s: sorted(rows) for s, rows in attacks.items()}
        for item in part['notes']:
            original = originals[(tuple(item['locations']), item['occurrence'])]
            n, before = item['note'], original['note']
            if n.get('mt') or n.get('tr'):
                continue
            end = before['t'] + before['sus']
            target = end
            for loc in original['locations']:
                for left, right in candidates.get(loc, ()):
                    if before['t']-1.1e-6 <= left <= end+1.1e-6:
                        target = max(target, right)
            # No visual extension: the ordinary note verifier already checks
            # this duration. Do not remap its rounded endpoint as new evidence.
            if target <= end+1e-6:
                continue
            if original['locations'][0] in short_origins:
                # Cut against the next physical attack in the source clock,
                # then apply the reference's staccato half-span rule.
                next_times = [r['note']['t'] for r in reference
                              if r['note']['s'] == before['s'] and r['note']['t'] > before['t']+1e-6]
                target = min(target, min(next_times, default=target))
                def q(seconds):
                    i = max(0, bisect_right(clock.seconds, seconds)-1)
                    return float(clock.positions[i]) + (seconds-float(clock.seconds[i]))*float(clock.bpms[i])/60
                target = float(clock.at((q(before['t'])+q(target))/2))
            target = min(float(mapping.at(target)), duration)
            following = attacks[n['s']]
            pos = bisect_right(following, n['t']+1e-8)
            if pos < len(following):
                target = min(target, following[pos])
            span = round(target-n['t'], 6)
            if span > n['sus']+1.1e-6:
                n['let_ring_sus'] = span
