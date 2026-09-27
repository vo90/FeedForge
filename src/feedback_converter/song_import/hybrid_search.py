"""Bounded optional whole-phrase search; primary decisions are already fixed.

Every edge depends only on the last complete passage. Scores are additive, so
one best state per ending passage is sufficient. Budgets retain a deterministic
feasible incumbent; they never turn an optional optimization into an import stop.
"""
from functools import lru_cache

EPS = 1e-7
UNITS = 1_000_000
SWITCH_COST = UNITS // 2
FRET_COST = UNITS // 100


def _key(p):
    return (p['end'], p['start'], p['trackId'],
            tuple((r['kind'], r['index']) for r in p.get('events', [])))


def select_passages(candidates, guard, preferred=(), *, max_candidates=20000,
                    max_states=10000, max_operations=500000,
                    left_anchor=None, right_anchor=None):
    """Return original candidate indices and transparent bounded-search facts.

    Explicit source preferences remain lexicographic. Otherwise useful source
    activity pays for source changes and hand movement. Passage span is never a
    reward: it can contain internal rests which must remain inside the phrase.
    Fixed surrounding primary events charge both entry and return transitions.
    Those costs are compared with leaving the gap empty, and diminish when an
    authored rest gives the player time to reposition. Anchors are already
    outside the caller's guarded candidate window and are never selectable.
    """
    indexed = sorted(enumerate(candidates), key=lambda item: _key(item[1]))
    nodes = [p for _, p in indexed]
    priorities = {tid: i for i, tid in enumerate(dict.fromkeys(preferred))}
    activity = [max(0, round(p.get('activeQuarterBeats', p['end'] - p['start']) * UNITS)) for p in nodes]
    empty = ((0,) * len(priorities), 0, 0, 0, 0, 0, 0)
    reasons = []
    if len(nodes) > max_candidates:
        reasons.append('candidate_limit')

    @lru_cache(maxsize=50000)
    def padding(q, direction):
        return guard(q, direction)

    def compatible(a, b):
        if a is None:
            return True
        left, right = nodes[a], nodes[b]
        if left['end'] > right['start'] + EPS:
            return False
        distance = 0 if left['trackId'] == right['trackId'] else max(
            padding(left['end'], 1), padding(right['start'], -1))
        return left['end'] + distance <= right['start'] + EPS

    def transition(left, right, anchored=False):
        if left is None or right is None or left['trackId'] == right['trackId']:
            return 0, 0, 0
        movement = 0
        if left.get('exitFret') is not None and right.get('entryFret') is not None:
            movement = abs(left['exitFret'] - right['entryFret']) * FRET_COST
        # A long real rest permits repositioning. Only fixed entry/return
        # edges receive this relief; donor-to-donor utility stays consistent.
        relief = max(1, right['start'] - left['end']) if anchored else 1
        return round((SWITCH_COST + movement) / relief), 1, round(movement / relief)

    baseline = transition(left_anchor, right_anchor, anchored=True)

    def extend(score, last, index):
        p = nodes[index]
        preferences = list(score[0])
        if p['trackId'] in priorities:
            preferences[priorities[p['trackId']]] += activity[index]
        cost, switched, movement = transition(left_anchor if last is None else nodes[last], p, anchored=last is None)
        confidence = sum(b in {'song', 'rest', 'section'} for b in p.get('boundaries', []))
        return (tuple(preferences), score[1] + activity[index] - cost,
                score[2] - switched, score[3] + activity[index], score[4] - 1,
                score[5] + confidence, score[6] - movement)

    def complete(score, last):
        cost, switched, movement = transition(nodes[last], right_anchor, anchored=True)
        return (score[0], score[1] - cost + baseline[0], score[2] - switched + baseline[1],
                score[3], score[4], score[5], score[6] - movement + baseline[2])

    best_score, best_path = empty, []
    # Two cheap complete paths remain available even if the exact search stops
    # early. Neither path depends on wall-clock timing or caller array order.
    quality_order = sorted(range(len(nodes)), key=lambda i: complete(extend(empty, None, i), i), reverse=True)
    end_order = sorted(quality_order, key=lambda i: (nodes[i]['end'], nodes[i]['start']))
    start_order = sorted(quality_order, key=lambda i: nodes[i]['start'])
    if max_candidates > 0:
        for order in (end_order, start_order):
            score, path, last = empty, [], None
            best_length = None
            for i in order:
                if compatible(last, i):
                    proposal = extend(score, last, i)
                    if proposal > score:
                        score, last = proposal, i
                        path.append(i)
                        finished = complete(score, last)
                        if finished > best_score:
                            best_score, best_length = finished, len(path)
            if best_length is not None:
                best_path = path[:best_length]

    states, parents = [], []
    operations = 0
    # A candidate limit limits the expensive graph, not the valid greedy
    # incumbent. An explicit zero budget retains only the primary/base result.
    if len(nodes) <= max_candidates and max_candidates > 0:
        for i, _ in enumerate(nodes):
            if len(states) >= max_states:
                reasons.append('state_limit')
                break
            score, parent = extend(empty, None, i), None
            exhausted = False
            for j, previous in enumerate(states):
                if operations >= max_operations:
                    reasons.append('operation_limit')
                    exhausted = True
                    break
                operations += 1
                if compatible(j, i):
                    proposal = extend(previous, j, i)
                    if proposal > score:
                        score, parent = proposal, j
            states.append(score)
            parents.append(parent)
            finished = complete(score, i)
            if finished > best_score:
                path, cursor = [], i
                while cursor is not None:
                    path.append(cursor)
                    cursor = parents[cursor]
                best_score, best_path = finished, list(reversed(path))
            if exhausted:
                break
    elif nodes and not reasons:
        reasons.append('candidate_limit')
    transitions = []
    if best_path:
        links = [(left_anchor, nodes[best_path[0]], True)]
        links += [(nodes[a], nodes[b], False) for a, b in zip(best_path, best_path[1:])]
        links.append((nodes[best_path[-1]], right_anchor, True))
        for left, right, anchored in links:
            if left is None or right is None:
                continue
            cost, switched, movement = transition(left, right, anchored)
            transitions.append({'fromTrackId': left['trackId'], 'toTrackId': right['trackId'],
                                'fixedBoundary': anchored, 'sourceChanged': bool(switched),
                                'restQuarterBeats': round(max(0, right['start'] - left['end']), 6),
                                'costQuarterBeats': round(cost / UNITS, 6),
                                'movementCostQuarterBeats': round(movement / UNITS, 6)})
    return {
        'indices': [indexed[i][0] for i in best_path],
        'activeQuarterBeats': round(best_score[3] / UNITS, 6),
        'utilityQuarterBeats': round(best_score[1] / UNITS, 6),
        'preferredQuarterBeats': [round(n / UNITS, 6) for n in best_score[0]],
        'switches': -best_score[2],
        'anchorTransitionBaselineQuarterBeats': round(baseline[0] / UNITS, 6),
        'transitions': transitions,
        'candidateCount': len(nodes), 'states': len(states), 'operations': operations,
        'budgetLimited': bool(reasons), 'reason': ','.join(dict.fromkeys(reasons)) or None,
    }
