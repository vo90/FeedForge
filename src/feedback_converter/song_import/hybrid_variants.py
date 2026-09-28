"""Bounded optional subphrases at complete written and performed boundaries.

The lead backbone and its guarded gaps are fixed before this module runs. A
variant changes source membership only; it never clips or modifies an event.
"""
from bisect import bisect_left, bisect_right
from copy import deepcopy
from math import isfinite

EPS = 1e-7
MIN_ACTIVE_QUARTERS = 4.0
MAX_VARIANTS = 4096
MAX_WINDOW_CHECKS = 100000
BOUNDARY_COST = {'bar': .125, 'gesture': .25}


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def _section_proof(quarter, rows, context, timeline, clock, charge):
    """A section label alone cannot veto or establish musical continuity."""
    from .hybrid_lead import _fingerprint

    measures = timeline['measures']
    index = next((i for i, m in enumerate(measures) if abs(m['quarter'] - quarter) <= EPS), None)
    if index is None or not timeline.get('tempoPoints'):
        return None
    for count in (1, 2, 4):
        if not charge():
            return None
        if index < count or index + count > len(measures):
            continue
        visits = measures[index-count:index+count]
        if any(not all(isinstance(m.get(k), int) and not isinstance(m[k], bool) and m[k] >= 0
                       for k in ('index', 'writtenIndex')) for m in visits):
            continue
        if any(b['index'] != a['index'] + 1 or b['writtenIndex'] != a['writtenIndex'] + 1
               or abs(a['quarter'] + a['quarters'] - b['quarter']) > EPS
               for a, b in zip(visits, visits[1:])):
            continue
        authored = context.get('authoredBarSignatures', [])
        if (any(m['writtenIndex'] >= len(authored) or not isinstance(authored[m['writtenIndex']], str)
                or not authored[m['writtenIndex']] for m in visits)
                or [authored[m['writtenIndex']] for m in visits[:count]] !=
                   [authored[m['writtenIndex']] for m in visits[count:]]):
            continue
        meter = visits[0].get('numerator'), visits[0].get('denominator')
        if (any(not isinstance(v, int) or isinstance(v, bool) or v <= 0 for v in meter)
                or any((m.get('numerator'), m.get('denominator')) != meter
                       or abs(m['quarters'] - meter[0] * 4 / meter[1]) > EPS for m in visits)):
            continue
        lo, hi = visits[0]['quarter'], visits[-1]['quarter'] + visits[-1]['quarters']
        if any(lo + EPS < q < hi - EPS and abs(q - quarter) > EPS
               for q in context.get('sectionQuarters', [])):
            continue
        tempo = timeline['tempoPoints']
        preceding = [p for p in tempo if p['quarter'] <= lo + EPS]
        if not preceding:
            continue
        bpm = max(preceding, key=lambda p: p['quarter']).get('bpm')
        if (not _number(bpm) or bpm <= 0
                or any(not _number(p.get('bpm')) or abs(p['bpm'] - bpm) > EPS
                       for p in tempo if lo + EPS < p['quarter'] < hi - EPS)):
            continue
        local_rows = [r for r in rows if r['start'] < hi - EPS and r['end'] > lo + EPS]
        local_beats = [b for b in context['beats'] if b['start'] < hi - EPS and b['end'] > lo + EPS]
        # The projection must account for every authored note in this proof.
        # Otherwise omitted material could make different phrases look equal.
        pairs = {(ident, occurrence) for r in local_rows for ident in r['sourceIds'] for occurrence in r['occurrences']}
        if any(not b.get('rest') and (not b.get('noteIds')
               or any((ident, b.get('occurrence')) not in pairs for ident in b['noteIds'])) for b in local_beats):
            continue
        if any(b['start'] < lo - EPS or b['end'] > hi + EPS
               or b['start'] < quarter - EPS and b['end'] > quarter + EPS for b in local_beats):
            continue
        def written(start, end):
            return sorted((round(b['start'] - start, 8), round(b['end'] - start, 8),
                           b.get('voice', 0), bool(b.get('rest')), bool(b.get('grace')))
                          for b in local_beats if start - EPS <= b['start'] < end - EPS)
        if written(lo, quarter) != written(quarter, hi):
            continue
        left = _fingerprint(local_rows, lo, quarter, clock)
        if left is not None and left == _fingerprint(local_rows, quarter, hi, clock):
            return {'quarter': quarter, 'barsPerSide': count, 'proofStart': lo, 'proofEnd': hi,
                    'meter': {'numerator': meter[0], 'denominator': meter[1]}, 'bpm': bpm}
    return None


def _components(spans):
    """Strict overlaps are inseparable; touching complete groups can separate."""
    result = []
    for start, end in sorted(spans):
        if result and start < result[-1][1] - EPS:
            result[-1][1] = max(result[-1][1], end)
        else:
            result.append([start, end])
    return result


def generate(parents, rows, context, timeline, clock, gaps, *, max_variants=MAX_VARIANTS,
             max_window_checks=MAX_WINDOW_CHECKS, adjacent_parents=None):
    """Offer at most four complete alternatives per rejected parent and gap.

    Maximal safe membership competes with nearby musical/bar boundaries. Long
    opportunities are considered first under the deterministic generation cap;
    callers keep every original phrase independently of this optional budget.
    """
    from .hybrid_lead import _hand_endpoints, union

    if not parents:
        return [], {'candidateCount': 0, 'windowChecks': 0, 'budgetLimited': False,
                    'minActiveQuarterBeats': MIN_ACTIVE_QUARTERS, 'minimumPitchedAttacks': 2}
    hard = _components([(r['start'], r['end']) for r in rows] +
                       [(b['start'], b['end']) for b in context['beats'] if not b['rest']])
    points = sorted({q for component in hard for q in component})
    # A zero-length component can lie within an occupied interval. It cannot
    # introduce a legal cut there merely because it has its own onset.
    hard_starts = [a for a, _ in hard]
    def clear(q):
        index = bisect_left(hard_starts, q - EPS) - 1
        return index < 0 or hard[index][1] <= q + EPS
    points = [q for q in points if clear(q)]
    bars = [m['quarter'] for m in timeline['measures']]
    labels = {}
    all_parents = parents if adjacent_parents is None else adjacent_parents
    for p in all_parents:
        for q, kind in zip(p['boundaryQuarters'], p['boundaries']):
            labels.setdefault(round(q, 7), kind)
    def label(q):
        known = labels.get(round(q, 7))
        if known:
            return known
        index = bisect_left(bars, q - EPS)
        return 'bar' if index < len(bars) and abs(bars[index] - q) <= EPS else 'gesture'
    active = union([(b['start'], b['end']) for b in context['beats'] if not b['rest']] +
                   [(clock.quarter(n['t']), clock.quarter(n['t'] + n.get('sus', 0)))
                    for r in rows for n in r['notes']])
    gap_ends = [hi for _, hi in gaps]
    opportunities, checks, limited = [], 0, False
    proof_cache = {}
    def charge():
        nonlocal checks, limited
        if checks >= max_window_checks:
            limited = True
            return False
        checks += 1
        return True
    rejected = {id(p) for p in parents}
    neighbors = {}
    for p in all_parents:
        index = bisect_right(gap_ends, p['start'] + EPS)
        while index < len(gaps) and gaps[index][0] < p['end'] - EPS:
            if checks >= max_window_checks:
                limited = True
                break
            checks += 1
            a, b = gaps[index]
            neighbors.setdefault(index, []).append(p)
            lo, hi = max(p['start'], a), min(p['end'], b)
            if id(p) in rejected and hi - lo >= MIN_ACTIVE_QUARTERS - EPS:
                opportunities.append((-(hi - lo), p['start'], p['end'], a, b, p))
            index += 1
        if limited:
            break
    # A repeat-bar partition is not a reason to reject two safe neighboring
    # pieces whose combined playing is substantial. Join only the parents
    # intersecting this one guarded gap, never across a source rest,
    # missing/unsupported parent or another source. Whole originals remain.
    for index, possible in sorted(neighbors.items()):
        runs, current = [], []
        ordered = sorted(possible, key=lambda item: (item['start'], item['end']))
        for p in ordered:
            previous = current[-1] if current else None
            adjacent = (previous is not None and previous['trackId'] == p['trackId']
                        and abs(previous['end'] - p['start']) <= EPS
                        and abs(previous['boundaryQuarters'][1] - p['boundaryQuarters'][0]) <= EPS
                        and previous['boundaries'][1] == p['boundaries'][0] == 'repeat')
            if current and not adjacent:
                runs.append(current)
                current = []
            current.append(p)
        if current:
            runs.append(current)
        a, b = gaps[index]
        for run in runs:
            if len(run) < 2 or not any(id(p) in rejected for p in run):
                continue
            first, last = run[0], run[-1]
            lo, hi = max(first['start'], a), min(last['end'], b)
            if hi - lo < MIN_ACTIVE_QUARTERS - EPS:
                continue
            combined = {**first, 'end': last['end'],
                        'boundaryQuarters': [first['boundaryQuarters'][0], last['boundaryQuarters'][1]],
                        'boundaries': [first['boundaries'][0], last['boundaries'][1]],
                        'events': [ref for p in run for ref in p['events']], 'parentPhraseCount': len(run)}
            opportunities.append((-(hi - lo), combined['start'], combined['end'], a, b, combined))
        # Section joins are separate two-parent alternatives, never an
        # extension of the unbounded repeat run above. Preserve the marker.
        for first, last in zip(ordered, ordered[1:]):
            q = first['boundaryQuarters'][1]
            if (first['trackId'] != last['trackId'] or not (id(first) in rejected or id(last) in rejected)
                    or first['boundaries'][1] != 'section' or last['boundaries'][0] != 'section'
                    or abs(first['end'] - last['start']) > EPS
                    or abs(first['end'] - q) > EPS or abs(last['start'] - q) > EPS
                    or abs(q - last['boundaryQuarters'][0]) > EPS
                    or not (a + EPS < q < b - EPS)
                    or any(first['boundaryQuarters'][0] + EPS < marker < last['boundaryQuarters'][1] - EPS
                           and abs(marker - q) > EPS for marker in context.get('sectionQuarters', []))
                    or not any(abs(marker - q) <= EPS for marker in context.get('sectionQuarters', []))):
                continue
            lo, hi = max(first['start'], a), min(last['end'], b)
            if hi - lo < MIN_ACTIVE_QUARTERS - EPS:
                continue
            if q not in proof_cache:
                proof_cache[q] = _section_proof(q, rows, context, timeline, clock, charge)
            if proof_cache[q] is None:
                continue
            combined = {**first, 'end': last['end'],
                        'boundaryQuarters': [first['boundaryQuarters'][0], last['boundaryQuarters'][1]],
                        'boundaries': [first['boundaries'][0], last['boundaries'][1]],
                        'events': first['events'] + last['events'], 'parentPhraseCount': 2,
                        'sectionJoin': {**deepcopy(proof_cache[q]), 'parents': [
                            {k: deepcopy(p[k]) for k in ('start', 'end', 'boundaryQuarters', 'boundaries')}
                            for p in (first, last)]}}
            opportunities.append((-(hi - lo), combined['start'], combined['end'], a, b, combined))
    variants, seen = [], set()
    for _, _, _, a, b, parent in sorted(opportunities, key=lambda item: item[:5]):
        lo, hi = max(parent['start'], a), min(parent['end'], b)
        inside = points[bisect_left(points, lo - EPS):bisect_right(points, hi + EPS)]
        if len(inside) < 2:
            continue
        first, last = inside[0], inside[-1]
        left_choices = [first]
        right_choices = [last]
        # Keep the closest stronger boundary, not every bar or every attack.
        stronger_left = next((q for q in inside if q > first + EPS and label(q) != 'gesture'), None)
        stronger_right = next((q for q in reversed(inside) if q < last - EPS and label(q) != 'gesture'), None)
        if label(first) == 'gesture' and stronger_left is not None:
            left_choices.append(stronger_left)
        if label(last) == 'gesture' and stronger_right is not None:
            right_choices.append(stronger_right)
        parent_keys = {(r['kind'], r['index']) for r in parent['events']}
        for start in left_choices:
            for end in right_choices:
                if end - start < MIN_ACTIVE_QUARTERS - EPS:
                    continue
                selected = [r for r in rows if r['start'] >= start - EPS and r['end'] <= end + EPS
                            and r['start'] < end - EPS and (r['kind'], r['index']) in parent_keys]
                if not selected:
                    continue
                keys = tuple((r['kind'], r['index']) for r in selected)
                if set(keys) == parent_keys or keys in seen:
                    continue
                actual_start = min(r['start'] for r in selected)
                actual_end = max(r['end'] for r in selected)
                if 'sectionJoin' in parent and not (actual_start + EPS < parent['sectionJoin']['quarter'] < actual_end - EPS):
                    continue
                activity = sum(max(0, min(y, actual_end) - max(x, actual_start)) for x, y in active)
                attacks = {round(n['t'], 8) for r in selected for n in r['notes']
                           if not n.get('ghost') and not n.get('mt') and 0 <= n['f'] <= 24}
                if activity < MIN_ACTIVE_QUARTERS - EPS or len(attacks) < 2:
                    continue
                if len(variants) >= max_variants:
                    return variants, {'candidateCount': len(variants), 'windowChecks': checks,
                                      'budgetLimited': True, 'minActiveQuarterBeats': MIN_ACTIVE_QUARTERS,
                                      'minimumPitchedAttacks': 2}
                seen.add(keys)
                boundary_labels = [label(start), label(end)]
                variants.append({'trackId': parent['trackId'], 'start': actual_start, 'end': actual_end,
                                 'activeQuarterBeats': round(activity, 8), 'ghostOnly': False,
                                 'boundaries': boundary_labels, 'boundaryQuarters': [start, end],
                                 'boundaryCostQuarterBeats': sum(BOUNDARY_COST.get(kind, 0) for kind in boundary_labels),
                                 **_hand_endpoints(selected),
                                 'events': [{k: deepcopy(r[k]) for k in ('kind', 'index', 'sourceIds', 'occurrences')}
                                            for r in selected],
                                 'variant': {'kind': 'gap_safe_subphrase', 'parentStart': parent['start'],
                                             'parentEnd': parent['end'],
                                             'parentBoundaryQuarters': deepcopy(parent['boundaryQuarters']),
                                             'parentBoundaries': deepcopy(parent['boundaries']),
                                             **({'parentPhraseCount': parent['parentPhraseCount']}
                                                if 'parentPhraseCount' in parent else {}),
                                             **({'sectionJoin': deepcopy(parent['sectionJoin'])}
                                                if 'sectionJoin' in parent else {})}})
    return variants, {'candidateCount': len(variants), 'windowChecks': checks,
                      'budgetLimited': limited, 'minActiveQuarterBeats': MIN_ACTIVE_QUARTERS,
                      'minimumPitchedAttacks': 2}
