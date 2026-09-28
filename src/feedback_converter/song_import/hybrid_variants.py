"""Bounded optional subphrases at complete written and performed boundaries.

The lead backbone and its guarded gaps are fixed before this module runs. A
variant changes source membership only; it never clips or modifies an event.
"""
from bisect import bisect_left, bisect_right
from copy import deepcopy

EPS = 1e-7
MIN_ACTIVE_QUARTERS = 4.0
MAX_VARIANTS = 4096
MAX_WINDOW_CHECKS = 100000
BOUNDARY_COST = {'bar': .125, 'gesture': .25}


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
    # intersecting this one guarded gap, never across a source rest, section,
    # missing/unsupported parent or another source. Whole originals remain.
    for index, possible in sorted(neighbors.items()):
        runs, current = [], []
        for p in sorted(possible, key=lambda item: (item['start'], item['end'])):
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
                                                if 'parentPhraseCount' in parent else {})}})
    return variants, {'candidateCount': len(variants), 'windowChecks': checks,
                      'budgetLimited': limited, 'minActiveQuarterBeats': MIN_ACTIVE_QUARTERS,
                      'minimumPitchedAttacks': 2}
