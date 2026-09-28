"""Independent raw-score evidence for optional joins across section markers."""
from bisect import bisect_right
from fractions import Fraction
import math

EPS = 1e-5


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def audit_section_join(passage, source, part, order, clock, events, check):
    """A producer label cannot authorize joining arbitrary source sections."""
    variant = passage['variant']
    proof = variant.get('sectionJoin')
    starts = [float(q) for q in clock.measure_starts] + [float(clock.quarters)]
    sections = {starts[i] for i, bi in enumerate(order) if source.bars[bi].section}
    occupied = [(starts[i] + float(b['q']), starts[i] + float(b['q'] + b['length']))
                for i, bi in enumerate(order) for b in part.beats[bi] if not b['rest']]

    def blocked(q):
        return (any(a + EPS < q < b - EPS for a, b in occupied)
                or any(row['start'] + EPS < q < row['end'] - EPS for row in events.values()))

    parent_bounds = variant.get('parentBoundaryQuarters', [])
    joined = variant.get('parentPhraseCount', 1)
    if proof is None:
        # Parent counts and proof labels are receipt claims, not authority to
        # cross a freely cuttable source section. A blocked marker can belong
        # to one natural parent because a complete gesture must remain intact.
        if (len(parent_bounds) == 2 and all(_number(q) for q in parent_bounds)
                and any(parent_bounds[0] + EPS < q < parent_bounds[1] - EPS and not blocked(q)
                        for q in sections)):
            check.fail('hybrid_section_join', 'hybrid/passages/variant', 'Joined sections lack independent continuity evidence.')
        return

    def fail(message):
        check.fail('hybrid_section_join', 'hybrid/passages/variant/sectionJoin', message)

    if not isinstance(proof, dict) or joined != 2:
        fail('A section join must identify exactly two neighboring source parents.')
        return
    q, size = proof.get('quarter'), proof.get('barsPerSide')
    parents = proof.get('parents')
    if (not all(_number(proof.get(k)) for k in ('quarter', 'proofStart', 'proofEnd', 'bpm'))
            or type(size) is not int or size not in (1, 2, 4)
            or not isinstance(parents, list) or len(parents) != 2
            or not all(isinstance(p, dict) for p in parents)):
        fail('Malformed bounded continuity proof.')
        return
    index = next((i for i, value in enumerate(starts[:-1]) if abs(value - q) <= EPS), None)
    if (index is None or q not in sections or index < size or index + size > len(order)
            or not passage['start'] + EPS < q < passage['end'] - EPS):
        fail('The selected passage must straddle a real section marker with complete proof bars.')
        return
    lo, hi = starts[index - size], starts[index + size]
    if abs(proof['proofStart'] - lo) > EPS or abs(proof['proofEnd'] - hi) > EPS:
        fail('Proof boundaries do not match the claimed source bars.')
    visited = order[index - size:index + size]
    if any(right != left + 1 for left, right in zip(visited, visited[1:])):
        fail('A repeat jump or alternate traversal interrupts the continuation.')
    meters = {source.bars[bi].signature for bi in visited}
    expected_meter = {'numerator': source.bars[visited[0]].signature[0],
                      'denominator': source.bars[visited[0]].signature[1]}
    if len(meters) != 1 or proof.get('meter') != expected_meter:
        fail('The source meter is not continuous.')
    tempo_index = max(0, bisect_right(clock.positions, Fraction(str(lo))) - 1)
    tempo = float(clock.bpms[tempo_index])
    if (abs(proof['bpm'] - tempo) > EPS
            or any(lo < float(at) < hi and abs(float(bpm) - tempo) > EPS
                   for at, bpm in zip(clock.positions, clock.bpms))):
        fail('The source tempo is not continuous.')
    if any(lo + EPS < marker < hi - EPS and abs(marker - q) > EPS for marker in sections):
        fail('Continuity evidence crosses another section marker.')
    # Exact raw written notes, techniques and beat structure, not the producer's
    # candidate fingerprints or a rounded density/expression score.
    from .verify_hybrid import _signature
    left = [_signature(part, bi) for bi in order[index - size:index]]
    right = [_signature(part, bi) for bi in order[index:index + size]]
    if left != right or not any(part.bars[bi] for bi in visited):
        fail('The raw written musical pattern does not repeat across the marker.')
    for parent in parents:
        bounds, labels = parent.get('boundaryQuarters'), parent.get('boundaries')
        if (not isinstance(bounds, list) or len(bounds) != 2 or not all(_number(v) for v in bounds)
                or not isinstance(labels, list) or len(labels) != 2
                or not all(_number(parent.get(k)) for k in ('start', 'end'))
                or not bounds[0] - EPS <= parent['start'] < parent['end'] <= bounds[1] + EPS):
            fail('Invalid original source-parent boundaries.')
            return
    first, last = parents
    if (first['boundaries'][1] != 'section' or last['boundaries'][0] != 'section'
            or any(abs(value - q) > EPS for value in (first['end'], last['start'],
                                                       first['boundaryQuarters'][1], last['boundaryQuarters'][0]))
            or parent_bounds != [first['boundaryQuarters'][0], last['boundaryQuarters'][1]]
            or abs(variant['parentStart'] - first['start']) > EPS
            or abs(variant['parentEnd'] - last['end']) > EPS):
        fail('The original source parents do not touch at the declared marker.')
    if any(first['start'] + EPS < marker < last['end'] - EPS and abs(marker - q) > EPS for marker in sections):
        fail('The joined source parents contain more than one section marker.')
    if blocked(q):
        fail('The source marker splits a written slot or connected musical gesture.')
