"""Bounded refinements of an already feasible optional passage plan.

Primary material is fixed by the caller. Neighbor refinement is additive: a
better graph result is accepted only if every incumbent source event survives.
"""
from copy import deepcopy
from bisect import bisect_left

EPS = 1e-7


def _key(p):
    return p['trackId'], tuple((r['kind'], r['index']) for r in p['events'])


def references(passages):
    return {(p['trackId'], r['kind'], r['index']) for p in passages for r in p['events']}


def _touch(a, b):
    return a['start'] < b['end'] - EPS and a['end'] > b['start'] + EPS


def _compatible(left, right, guard):
    padding = 0 if left['trackId'] == right['trackId'] else max(guard(left['end'], 1), guard(right['start'], -1))
    return left['end'] + padding <= right['start'] + EPS


def _retained_boundary(parent, episode, source, timeline, clock, guard, side):
    """Keep a short outside edge of an already selected complete passage.

    This is not a new small-fill opportunity. The retained fragment reaches the
    incumbent's outer edge and is adjacent to the promoted complete episode.
    """
    from .hybrid_variants import _components, BOUNDARY_COST
    from .hybrid_lead import _hand_endpoints, union

    if parent.get('acceptedEnding') or parent.get('variant', {}).get('kind') == 'retained_optional_boundary':
        return None
    if not (parent['start'] < episode['start' if side == 'before' else 'end'] - EPS < parent['end']):
        return None
    rows, context = source['rows'], source['context']
    hard = _components([(r['start'], r['end']) for r in rows]
                       + [(b['start'], b['end']) for b in context['beats'] if not b['rest']])
    starts = [a for a, _ in hard]
    def clear(q):
        i = bisect_left(starts, q - EPS) - 1
        return i < 0 or hard[i][1] <= q + EPS
    points = sorted({q for a, b in hard for q in (a, b) if clear(q)})
    original_keys = {(r['kind'], r['index']) for r in parent['events']}
    selected = None
    for cut in (reversed(points) if side == 'before' else points):
        a, b = (parent['start'], cut) if side == 'before' else (cut, parent['end'])
        if a < parent['start'] - EPS or b > parent['end'] + EPS or b <= a + EPS:
            continue
        probe = {'trackId': parent['trackId'], 'start': a, 'end': b}
        if not (_compatible(probe, episode, guard) if side == 'before' else _compatible(episode, probe, guard)):
            continue
        selected = [r for r in rows if (r['kind'], r['index']) in original_keys
                    and a - EPS <= r['start'] and r['end'] <= b + EPS and r['start'] < b - EPS]
        if selected:
            break
    if not selected:
        return None
    start, end = min(r['start'] for r in selected), max(r['end'] for r in selected)
    if abs((start if side == 'before' else end) - (parent['start'] if side == 'before' else parent['end'])) > EPS:
        return None
    attacks = {round(n['t'], 8) for r in selected for n in r['notes']
               if not n.get('ghost') and not n.get('mt') and 0 <= n['f'] <= 24}
    if len(attacks) < 2:
        return None
    cut = end if side == 'before' else start
    boundary = 'bar' if any(abs(m['quarter'] - cut) <= EPS for m in timeline['measures']) else 'gesture'
    labels = [parent['boundaries'][0], boundary] if side == 'before' else [boundary, parent['boundaries'][1]]
    bounds = [parent['boundaryQuarters'][0], cut] if side == 'before' else [cut, parent['boundaryQuarters'][1]]
    active = union([(b['start'], b['end']) for b in context['beats'] if not b['rest']]
                   + [(clock.quarter(n['t']), clock.quarter(n['t'] + n.get('sus', 0))) for r in rows for n in r['notes']])
    parent_proof = {k: deepcopy(parent[k]) for k in ('trackId', 'start', 'end', 'boundaries', 'boundaryQuarters', 'events', 'variant') if k in parent}
    return {'trackId': parent['trackId'], 'start': start, 'end': end, 'ghostOnly': False,
            'activeQuarterBeats': round(sum(max(0, min(y, end) - max(x, start)) for x, y in active), 8),
            'boundaries': labels, 'boundaryQuarters': bounds,
            'boundaryCostQuarterBeats': sum(BOUNDARY_COST.get(label, 0) for label in labels),
            **_hand_endpoints(selected),
            'events': [{k: deepcopy(r[k]) for k in ('kind', 'index', 'sourceIds', 'occurrences')} for r in selected],
            'variant': {'kind': 'retained_optional_boundary', 'parentStart': parent['start'], 'parentEnd': parent['end'],
                        'parentBoundaryQuarters': deepcopy(parent['boundaryQuarters']),
                        'parentBoundaries': deepcopy(parent['boundaries']), 'retainedParent': parent_proof,
                        'foregroundEpisode': {k: deepcopy(episode[k]) for k in ('trackId', 'start', 'end', 'events')},
                        'retentionSide': side}}


def _edge_omissions(missing, selected, episodes, candidates, sources, guard):
    """Account for at most one beat of isolated backing at episode edges.

    A one-attack remainder is too small to retain as a boundary fragment. It
    may be omitted only within the same evidenced local backing/foreground
    choice, never from unrelated passages or from the fixed primary layer.
    """
    from .hybrid_lead import union
    if not missing:
        return set(), []
    allowed, records = set(), []
    for proposal in episodes:
        episode = candidates[proposal['candidateIndex']]
        competing = {_key(candidates[i]) for i in proposal.get('competingCandidateIndices', [])}
        episode_records, episode_refs = [], set()
        for parent in selected:
            if parent.get('acceptedEnding') or _key(parent) not in competing:
                continue
            lookup = {(r['kind'], r['index']): r for r in sources[parent['trackId']]['rows']}
            for side in ('before', 'after'):
                boundary = episode['start' if side == 'before' else 'end']
                outer = parent['start' if side == 'before' else 'end']
                if abs(outer - boundary) > 1 + guard(boundary, -1 if side == 'before' else 1) + EPS:
                    continue
                refs = [r for r in parent['events'] if (parent['trackId'], r['kind'], r['index']) in missing
                        and (lookup[(r['kind'], r['index'])]['end'] <= boundary + EPS if side == 'before'
                             else lookup[(r['kind'], r['index'])]['start'] >= boundary - EPS)]
                members = [lookup[(r['kind'], r['index'])] for r in refs]
                if not members or any(n.get('ghost') for r in members for n in r['notes']):
                    continue
                spans = union((r['start'], r['end']) for r in members)
                if len(spans) != 1 or abs(spans[0][0 if side == 'before' else 1] - outer) > EPS:
                    continue
                attacks = {round(n['t'], 8) for r in members for n in r['notes']
                           if not n.get('mt') and 0 <= n['f'] <= 24}
                if len(attacks) >= 2:
                    continue
                episode_refs.update((parent['trackId'], r['kind'], r['index']) for r in refs)
                episode_records.append({'trackId': parent['trackId'], 'side': side,
                    'start': spans[0][0], 'end': spans[0][1], 'quarterBeats': round(spans[0][1] - spans[0][0], 8),
                    'pitchedAttackCount': len(attacks), 'events': deepcopy(refs),
                    'incumbentParent': {k: deepcopy(parent[k]) for k in ('trackId', 'start', 'end', 'boundaries', 'boundaryQuarters', 'events', 'variant') if k in parent},
                    'foregroundEpisode': {k: deepcopy(episode[k]) for k in ('trackId', 'start', 'end', 'events')}})
        duration = sum(r['quarterBeats'] for r in episode_records)
        if duration <= min(1.0, .2 * (episode['end'] - episode['start'])) + EPS:
            allowed.update(episode_refs)
            records.extend(episode_records)
    return allowed, records


def refine_foreground(candidates, incumbent, sources, tracks, timeline, clock, gap, guard,
                      preferred=(), *, options, main_id, validate, max_variants, max_window_checks,
                      max_candidates, max_states, max_operations, max_recognition_candidates,
                      max_recognition_operations, max_episodes, left_anchor=None, right_anchor=None):
    """Promote complete evidenced melody, preserving outside incumbent music."""
    from .hybrid_optional_foreground import recognize
    from .hybrid_variants import generate
    from .hybrid_search import select_passages

    report = {'candidateCount': 0, 'windowChecks': 0, 'states': 0, 'operations': 0,
              'budgetLimited': False, 'reason': None, 'episodes': [], 'accepted': False}
    recognition = recognize(candidates, {tid: s['rows'] for tid, s in sources.items()}, tracks, clock,
                            options=options, main_id=main_id, max_candidates=max_recognition_candidates,
                            max_operations=max_recognition_operations, max_episodes=max_episodes)
    report['recognition'] = recognition
    if recognition['budgetLimited']:
        report.update(budgetLimited=True, reason='optional_foreground_' + recognition['reason'])
        return candidates, incumbent, report
    selected = [candidates[i] for i in incumbent['indices']]
    old_refs = references(selected)
    episodes = [e for e in recognition['episodes'] if not references([candidates[e['candidateIndex']]]) <= old_refs]
    if not episodes:
        return candidates, incumbent, report
    if max_variants <= 0 or max_window_checks <= 0 or len(candidates) >= max_candidates:
        report.update(budgetLimited=True, reason='foreground_generation_limit')
        return candidates, incumbent, report
    targets = [candidates[e['candidateIndex']] for e in episodes]
    target_keys = {_key(p) for p in targets}
    viable = [p for p in candidates if _key(p) in target_keys or not any(_touch(p, e) for e in targets)]
    added, seen = [], {_key(p) for p in candidates}
    for tid in sorted({p['trackId'] for p in candidates}):
        source = sources[tid]
        admitted = {(r['kind'], r['index']) for p in candidates if p['trackId'] == tid for r in p['events']}
        parents = [p for p in source['parents'] if any((r['kind'], r['index']) in admitted for r in p['events'])]
        windows = []
        for left, right in zip([None, *targets], [*targets, None]):
            if report['windowChecks'] >= max_window_checks:
                report.update(budgetLimited=True, reason='foreground_generation_limit')
                return candidates, incumbent, report
            report['windowChecks'] += 1
            lo, hi = (max(gap[0], left['end']) if left else gap[0]), (min(gap[1], right['start']) if right else gap[1])
            if left and left['trackId'] != tid:
                lo = max(lo, left['end'] + guard(left['end'], 1))
            if right and right['trackId'] != tid:
                hi = min(hi, right['start'] - guard(right['start'], -1))
            if hi - lo >= 4 - EPS:
                windows.append((lo, hi))
        generated, info = generate(parents, source['rows'], source['context'], timeline, clock, windows,
                                   max_variants=max(0, min(max_variants - report['candidateCount'], max_candidates - len(candidates) - len(added))),
                                   max_window_checks=max(0, max_window_checks - report['windowChecks']), adjacent_parents=parents)
        report['candidateCount'] += info['candidateCount']
        report['windowChecks'] += info['windowChecks']
        if info['budgetLimited']:
            report.update(budgetLimited=True, reason='foreground_generation_limit')
            return candidates, incumbent, report
        for p in generated:
            if _key(p) not in seen and validate(p, source) == 'eligible_phrase' and not any(_touch(p, e) for e in targets):
                seen.add(_key(p)); added.append(p)
    # At most the two immediately neighboring edges of actual incumbent parents
    # receive the narrow short-fragment exception. No recursive tiny fragments.
    for episode in targets:
        for side in ('before', 'after'):
            if report['windowChecks'] >= max_window_checks or report['candidateCount'] >= max_variants or len(candidates) + len(added) >= max_candidates:
                report.update(budgetLimited=True, reason='foreground_generation_limit')
                return candidates, incumbent, report
            report['windowChecks'] += 1
            edge = episode['start' if side == 'before' else 'end']
            parent = next((p for p in selected if p['start'] < edge - EPS < p['end'] and p['trackId'] != episode['trackId']), None)
            if parent is None:
                continue
            source = sources[parent['trackId']]
            construction = len(source['rows']) + len(source['context']['beats'])
            if construction > max_window_checks - report['windowChecks']:
                report.update(budgetLimited=True, reason='foreground_generation_limit')
                return candidates, incumbent, report
            report['windowChecks'] += construction
            p = _retained_boundary(parent, episode, source, timeline, clock, guard, side)
            if p and _key(p) not in seen and validate(p, sources[p['trackId']]) == 'eligible_phrase' and not any(_touch(p, e) for e in targets):
                seen.add(_key(p)); added.append(p)
                report['candidateCount'] += 1
    graph = [*viable, *added]
    trial = select_passages(graph, guard, preferred, max_candidates=max_candidates, max_states=max_states,
                            max_operations=max_operations, left_anchor=left_anchor, right_anchor=right_anchor)
    report['states'], report['operations'] = trial['states'], trial['operations']
    if trial['reason']:
        report.update(budgetLimited=True, reason='optional_foreground_' + trial['reason'])
        return candidates, incumbent, report
    picked = [graph[i] for i in trial['indices']]
    picked_refs = references(picked)
    outside = set()
    for parent in selected:
        # Terminal acceptance proves this complete phrase with every attack
        # retained. It cannot authorize the foreground's tiny-edge omissions.
        if parent.get('acceptedEnding'):
            outside.update(references([parent]))
            continue
        source = sources[parent['trackId']]
        lookup = {(r['kind'], r['index']): r for r in source['rows']}
        for ref in parent['events']:
            row = lookup[(ref['kind'], ref['index'])]
            replaceable = False
            for episode in targets:
                lo, hi = episode['start'], episode['end']
                if parent['trackId'] != episode['trackId']:
                    lo -= max(guard(lo, -1), guard(row['end'], 1))
                    hi += max(guard(hi, 1), guard(row['start'], -1))
                if row['start'] < hi - EPS and row['end'] > lo + EPS:
                    replaceable = True
                    break
            if not replaceable:
                outside.add((parent['trackId'], ref['kind'], ref['index']))
    lost_activity = incumbent['activeQuarterBeats'] - trial['activeQuarterBeats']
    allowed_loss = max(1.0, .25 * sum(p['end'] - p['start'] for p in targets))
    edge_refs, edge_records = _edge_omissions(outside - picked_refs, selected, episodes, candidates, sources, guard)
    report['activityChangeQuarterBeats'] = round(-lost_activity, 6)
    report['allowedActivityLossQuarterBeats'] = round(allowed_loss, 6)
    explicit_priority_kept = tuple(trial['preferredQuarterBeats']) >= tuple(incumbent['preferredQuarterBeats'])
    if not references(targets) <= picked_refs or not outside <= picked_refs | edge_refs or lost_activity > allowed_loss + EPS or not explicit_priority_kept:
        report['rejectedReason'] = ('foreground_not_selected' if not references(targets) <= picked_refs else
                                    'outside_incumbent_material_changed' if not outside <= picked_refs | edge_refs else
                                    'explicit_source_priority_regression' if not explicit_priority_kept else 'excessive_activity_loss')
        report['missingOutsideCount'] = len(outside - picked_refs - edge_refs)
        report['missingOutsideReferences'] = sorted(outside - picked_refs - edge_refs)[:32]
        return candidates, incumbent, report
    expanded = [*candidates, *added]
    positions = {_key(p): i for i, p in enumerate(expanded)}
    trial['indices'] = [positions[_key(p)] for p in picked]
    report['accepted'] = True
    report['activityChangeQuarterBeats'] = round(-lost_activity, 6)
    report['outsidePreservedEventCount'] = len(outside & picked_refs)
    report['allowedEdgeOmissions'] = edge_records
    report['beforeActiveQuarterBeats'] = incumbent['activeQuarterBeats']
    report['afterActiveQuarterBeats'] = trial['activeQuarterBeats']
    report['episodes'] = [{**deepcopy(e), 'events': deepcopy(candidates[e['candidateIndex']]['events']),
                           'incumbentPassages': [{k: deepcopy(p[k]) for k in ('trackId', 'start', 'end', 'boundaries', 'boundaryQuarters', 'events', 'variant') if k in p}
                                                 for p in selected if _touch(p, candidates[e['candidateIndex']])]}
                          for e in episodes]
    return expanded, trial, report


def refine_neighbors(candidates, incumbent, sources, timeline, clock, gap, guard,
                     preferred=(), *, validate, max_variants, max_window_checks,
                     max_candidates, max_states, max_operations,
                     left_anchor=None, right_anchor=None, edge_omissions=()):
    """Offer original-parent alternatives around one fixed incumbent schedule.

    Use the full gap graph for the second search: optional neighbors must not
    acquire the rest/movement relief reserved for fixed primary anchors.
    """
    from .hybrid_variants import generate
    from .hybrid_search import select_passages

    report = {'candidateCount': 0, 'windowChecks': 0, 'budgetLimited': False,
              'states': 0, 'operations': 0, 'accepted': False, 'reason': None}
    selected = sorted((candidates[i] for i in incumbent['indices']),
                      key=lambda p: (p['start'], p['end'], p['trackId']))
    if not selected or not candidates:
        return candidates, incumbent, report
    if len(candidates) >= max_candidates or max_variants <= 0 or max_window_checks <= 0:
        report.update(budgetLimited=True, reason='neighbor_generation_limit')
        return candidates, incumbent, report
    by_track = {}
    for p in candidates:
        by_track.setdefault(p['trackId'], set()).update((r['kind'], r['index']) for r in p['events'])
    existing, added = {_key(p) for p in candidates}, []
    lo_gap, hi_gap = gap
    for tid, admitted in sorted(by_track.items()):
        source = sources[tid]
        # Resolve a fixed-primary-safe variant to real original parents. This
        # also handles repeat/section unions without nesting fabricated parents.
        parents = [p for p in source['parents'] if any((r['kind'], r['index']) in admitted for r in p['events'])]
        if not parents:
            continue
        windows = []
        for left, right in zip([None, *selected], [*selected, None]):
            if report['windowChecks'] >= max_window_checks:
                report.update(budgetLimited=True, reason='neighbor_generation_limit')
                break
            report['windowChecks'] += 1
            lo = max(lo_gap, left['end']) if left else lo_gap
            hi = min(hi_gap, right['start']) if right else hi_gap
            if left and left['trackId'] != tid:
                lo = max(lo, left['end'] + guard(left['end'], 1))
            if right and right['trackId'] != tid:
                hi = min(hi, right['start'] - guard(right['start'], -1))
            if hi - lo >= 4 - EPS:
                windows.append((lo, hi))
        if not windows:
            if report['budgetLimited']:
                break
            continue
        generated, info = generate(parents, source['rows'], source['context'], timeline, clock, windows,
                                   max_variants=max(0, min(max_variants - report['candidateCount'],
                                                         max_candidates - len(candidates) - len(added))),
                                   max_window_checks=max(0, max_window_checks - report['windowChecks']),
                                   adjacent_parents=parents)
        report['candidateCount'] += info['candidateCount']
        report['windowChecks'] += info['windowChecks']
        report['budgetLimited'] |= info['budgetLimited']
        for p in generated:
            identity = _key(p)
            if identity not in existing and validate(p, source) == 'eligible_phrase':
                existing.add(identity)
                added.append(p)
        if report['budgetLimited']:
            report['reason'] = 'neighbor_generation_limit'
            break
    if not added:
        return candidates, incumbent, report
    expanded = [*candidates, *added]
    trial = select_passages(expanded, guard, preferred, max_candidates=max_candidates,
                            max_states=max_states, max_operations=max_operations,
                            left_anchor=left_anchor, right_anchor=right_anchor)
    report['states'], report['operations'] = trial['states'], trial['operations']
    report['budgetLimited'] |= trial['budgetLimited']
    if trial['reason']:
        report['reason'] = ','.join(filter(None, (report['reason'], trial['reason'])))
    trial_refs = references(expanded[i] for i in trial['indices'])
    survives = references(selected) <= trial_refs
    improves = (tuple(trial['preferredQuarterBeats']) >= tuple(incumbent['preferredQuarterBeats'])
                and trial['utilityQuarterBeats'] >= incumbent['utilityQuarterBeats'] - EPS)
    if survives and improves:
        # Retained accompaniment and disclosed edge omissions refer to an
        # exact selected foreground episode. Keeping its notes inside a larger
        # candidate does not preserve that independently checked provenance.
        proof_episodes = [p['variant']['foregroundEpisode'] for p in selected
                          if p.get('variant', {}).get('kind') == 'retained_optional_boundary']
        proof_episodes.extend(r['foregroundEpisode'] for r in edge_omissions if r.get('foregroundEpisode'))
        picked = [expanded[i] for i in trial['indices']]
        for episode in proof_episodes:
            episode_refs = references([episode])
            if not any(p['trackId'] == episode['trackId']
                       and abs(p['start'] - episode['start']) <= EPS
                       and abs(p['end'] - episode['end']) <= EPS
                       and references([p]) == episode_refs for p in picked):
                report['rejectedReason'] = 'foreground_episode_resegmented'
                return expanded, deepcopy(incumbent), report
        restored = []
        for index, omission in enumerate(edge_omissions):
            omitted = {(omission['trackId'], r['kind'], r['index']) for r in omission['events']}
            if omitted & trial_refs:
                if not omitted <= trial_refs:
                    report['rejectedReason'] = 'partial_omission_restoration'
                    return expanded, deepcopy(incumbent), report
                restored.append(index)
        report['restoredEdgeOmissionIndices'] = restored
        report['accepted'] = trial_refs != references(selected)
        return expanded, trial, report
    report['rejectedReason'] = 'incumbent_material_changed' if not survives else 'utility_regression'
    return expanded, deepcopy(incumbent), report
