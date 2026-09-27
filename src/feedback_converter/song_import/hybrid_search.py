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
                    max_states=10000, max_operations=500000):
    """Return original candidate indices and transparent bounded-search facts.

    Explicit source preferences remain lexicographic. Otherwise useful source
    activity pays for source changes and hand movement. Passage span is never a
    reward: it can contain internal rests which must remain inside the phrase.
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

    def extend(score, last, index):
        p = nodes[index]
        preferences = list(score[0])
        if p['trackId'] in priorities:
            preferences[priorities[p['trackId']]] += activity[index]
        switched = last is not None and nodes[last]['trackId'] != p['trackId']
        movement = 0
        if switched and nodes[last].get('exitFret') is not None and p.get('entryFret') is not None:
            movement = round(abs(nodes[last]['exitFret'] - p['entryFret']) * FRET_COST)
        confidence = sum(b in {'song', 'rest', 'section'} for b in p.get('boundaries', []))
        return (tuple(preferences), score[1] + activity[index] - int(switched) * SWITCH_COST - movement,
                score[2] - int(switched), score[3] + activity[index], score[4] - 1,
                score[5] + confidence, score[6] - movement)

    best_score, best_path = empty, []
    # Two cheap complete paths remain available even if the exact search stops
    # early. Neither path depends on wall-clock timing or caller array order.
    start_order = sorted(range(len(nodes)), key=lambda i: (
        nodes[i]['start'], priorities.get(nodes[i]['trackId'], len(priorities)),
        -activity[i], _key(nodes[i])))
    if max_candidates > 0:
        for order in (range(len(nodes)), start_order):
            score, path, last = empty, [], None
            for i in order:
                if compatible(last, i):
                    proposal = extend(score, last, i)
                    if proposal > score:
                        score, last = proposal, i
                        path.append(i)
            if score > best_score:
                best_score, best_path = score, path

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
            if score > best_score:
                path, cursor = [], i
                while cursor is not None:
                    path.append(cursor)
                    cursor = parents[cursor]
                best_score, best_path = score, list(reversed(path))
            if exhausted:
                break
    elif nodes and not reasons:
        reasons.append('candidate_limit')
    return {
        'indices': [indexed[i][0] for i in best_path],
        'activeQuarterBeats': round(best_score[3] / UNITS, 6),
        'utilityQuarterBeats': round(best_score[1] / UNITS, 6),
        'preferredQuarterBeats': [round(n / UNITS, 6) for n in best_score[0]],
        'switches': -best_score[2],
        'candidateCount': len(nodes), 'states': len(states), 'operations': operations,
        'budgetLimited': bool(reasons), 'reason': ','.join(dict.fromkeys(reasons)) or None,
    }
