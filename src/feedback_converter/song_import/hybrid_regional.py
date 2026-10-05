"""Regional primary composition, with immutable setup and complete source events.

Selection returns references only. Physical source-copy checks are performed by
the independent verifier, not by trusting these ranking decisions.
"""
from copy import deepcopy
from bisect import bisect_left
import hashlib
import json

from .alignment import map_time
from .hybrid_context import Clock

EPS = 1e-7
MAX_PRIMARY_CANDIDATES = 4096
MAX_PRIMARY_EDGES = 1000000
MAX_PRIMARY_VARIANTS = 4096


def ref(row):
    return {k: deepcopy(row[k]) for k in ('kind', 'index', 'sourceIds', 'occurrences')}


def overlap(a, b):
    return a[0] < b[1] - EPS and a[1] > b[0] + EPS or a[0] == a[1] and b[0] - EPS <= a[0] < b[1] - EPS


def _key(row):
    return row['kind'], row['index']


def _in_unsupported_gesture(row, limitation):
    return (bool(set(row['sourceIds']) & set(limitation['sourceIds']))
            and bool(set(row['occurrences']) & set(limitation['occurrences'])))


def unsupported_regions(performance, clock):
    """Locate whole raw technique groups before high-fret projection loses them."""
    from .high_frets import reason
    from .hybrid_lead import event_rows
    context = performance['compositionContext']['tracks']
    result = []
    for track in performance.get('hybridRawTracks', []):
        if track['instrument'] != 'guitar' or track['id'] not in context:
            continue
        rows = event_rows(track, context[track['id']], clock)
        bad = {(r['start'], r['end']) for r in rows if any(reason(n) for n in r['notes'])}
        for lo, hi in sorted(bad):
            members = [r for r in rows if (r['start'], r['end']) == (lo, hi)]
            result.append({'trackId': track['id'], 'start': lo, 'end': hi,
                           'reason': 'unsupported_source_gesture',
                           'sourceIds': sorted({s for r in members for s in r['sourceIds']}),
                           'occurrences': sorted({o for r in members for o in r['occurrences']})})
    return result


def _windows(lo, hi, blockers):
    from .hybrid_lead import union
    result, cursor = [], lo
    for a, b in union(blockers):
        if b <= cursor + EPS or a >= hi - EPS:
            continue
        if a > cursor + EPS:
            result.append((cursor, min(a, hi)))
        cursor = max(cursor, b)
    if hi > cursor + EPS:
        result.append((cursor, hi))
    return result


def _members(rows, clock, lo, hi):
    # The attack window and complete footprint are deliberately different.
    # Closing hard components cannot pull unrelated later backing attacks into
    # a solo merely because they begin under a sustained ending.
    seeds = [r for r in rows if lo - EPS <= min(clock.quarter(n['t']) for n in r['notes']) < hi - EPS]
    groups = {(r['start'], r['end']) for r in seeds}
    return [r for r in rows if (r['start'], r['end']) in groups]


def _passage(candidate, rows, clock, lo, hi):
    selected = _members(rows, clock, lo, hi)
    if not selected or any(not r['available'] for r in selected):
        return None
    start, end = min(r['start'] for r in selected), max(r['end'] for r in selected)
    if end <= start + EPS:
        return None
    return {'trackId': candidate['trackId'], 'start': start, 'end': end,
            'ownedStart': lo, 'ownedEnd': hi, 'priority': candidate.get('priority', 'lead'),
            'evidence': candidate['evidence'], 'confidence': candidate.get('confidence', 'medium'),
            'sectionName': candidate.get('sectionName', ''),
            'obligationId': candidate['id'], 'events': [ref(r) for r in selected],
            'boundaries': ['primary', 'primary'], 'boundaryQuarters': [start, end],
            'entryFret': None, 'exitFret': None}


def _score(p, candidate, main_id, preferred, required=None):
    whole = (bool(required) and required <= {_key(r) for r in p['events']}) if required is not None else (
        p['ownedStart'] <= candidate.get('ownedStart', candidate['start']) + EPS
        and p['ownedEnd'] >= candidate.get('ownedEnd', candidate['end']) - EPS)
    span = p['ownedEnd'] - p['ownedStart']
    evidence = candidate['evidence']
    # Complete named episodes outrank any amount of unnamed filler. Full
    # alternatives overlap, so an episode cannot receive duplicate full credit.
    rank = candidate.get('score', 0)
    if isinstance(rank, (list, tuple)):
        rank = sum(float(value) / (100 ** i) for i, value in enumerate(rank) if isinstance(value, (int, float)))
    elif not isinstance(rank, (int, float)):
        rank = 0
    return (int(whole and evidence == 'named_soloist'), span if evidence == 'named_soloist' else 0,
            *(span if p['trackId'] == tid else 0 for tid in preferred),
            # A labelled Guitar Solo also outranks an inferred chorus/riff.
            # Otherwise a long preceding tail plus a truncated solo can score
            # more total activity than preserving the complete solo opening.
            span if evidence == 'dedicated_solo' or candidate.get('labelledSolo') else 0, span,
            float(rank) * span, int(p['trackId'] == main_id) * .05 * span, -1)


def _candidate_score(p, by_id, main_id, preferred, requirements):
    value = (0,) * (7 + len(preferred))
    for ident in dict.fromkeys(p.get('obligationIds', [p['obligationId']])):
        candidate = by_id[ident]
        owned = {**p, 'ownedStart': max(p['ownedStart'], candidate.get('ownedStart', candidate['start'])),
                 'ownedEnd': min(p['ownedEnd'], candidate.get('ownedEnd', candidate['end']))}
        if owned['ownedEnd'] <= owned['ownedStart'] + EPS:
            continue
        local = _score(owned, candidate, main_id, preferred, requirements.get(ident))
        value = tuple(a + b for a, b in zip(value, local))
    # A composite is one physical passage even when it satisfies several
    # distinct semantic obligations. No obligation receives repeated credit.
    return (*value[:-1], -1)


def _coalesced(candidates, rows, clock, by_id, main_id, preferred, requirements):
    """Join safe same-source polyphony across touching ownership windows.

    Only maximal components of mandatory candidates are added. Originals stay
    available, and optional overlap variants cannot cause quadratic expansion.
    The exact membership check forbids bridging missing/unsupported source rows.
    """
    ordered = sorted(candidates, key=lambda p: (p['trackId'], p['ownedStart'], p['ownedEnd'], p['start'], p['end']))
    groups, group = [], []
    owned_end = footprint_end = None
    for p in ordered:
        if group and (p['trackId'] != group[0]['trackId'] or p['ownedStart'] > owned_end + EPS
                      or p['start'] >= footprint_end - EPS):
            groups.append(group)
            group = []
        owned_end = max(owned_end, p['ownedEnd']) if group else p['ownedEnd']
        footprint_end = max(footprint_end, p['end']) if group else p['end']
        group.append(p)
    if group:
        groups.append(group)
    result = []
    for group in groups:
        if len(group) < 2:
            continue
        lo, hi = min(p['ownedStart'] for p in group), max(p['ownedEnd'] for p in group)
        members = {_key(r): r for p in group for r in p['events']}
        expected = _members(rows[group[0]['trackId']], clock, lo, hi)
        if set(members) != {_key(r) for r in expected} or any(not r['available'] for r in expected):
            continue
        strongest = max(group, key=lambda p: _candidate_score(p, by_id, main_id, preferred, requirements))
        result.append({**deepcopy(strongest), 'start': min(p['start'] for p in group),
                       'end': max(p['end'] for p in group), 'ownedStart': lo, 'ownedEnd': hi,
                       'events': [ref(r) for r in expected],
                       'obligationIds': sorted({ident for p in group for ident in p.get('obligationIds', [p['obligationId']])}),
                       'boundaryQuarters': [min(p['start'] for p in group), max(p['end'] for p in group)]})
    return result


def _select(candidates, evidence, main_id, preferred, requirements=None):
    """Bounded DAG with a complete-song strong-primary incumbent."""
    candidates.sort(key=lambda p: (p['end'], p['start'], p['trackId'], p['ownedStart'], p['ownedEnd']))
    by_id = {c['id']: c for c in evidence}
    requirements = requirements or {}
    local_scores = [_candidate_score(p, by_id, main_id, preferred, requirements) for p in candidates]
    scores, parents, edges = [], [], 0
    zero = (0,) * (7 + len(preferred))
    # Greedily reserve strong complete episodes throughout the song first.
    # Optional variants can then occupy free intervals. Unlike an early prefix,
    # this incumbent does not lose every later solo when an edge budget expires.
    selected, starts = [], []
    order = sorted(range(len(candidates)), key=lambda i: local_scores[i], reverse=True)
    for i in order:
        p = candidates[i]
        position = bisect_left(starts, p['start'])
        if (position and candidates[selected[position - 1]]['end'] > p['start'] + EPS
                or position < len(selected) and p['end'] > candidates[selected[position]]['start'] + EPS):
            continue
        selected.insert(position, i)
        starts.insert(position, p['start'])
    best_score = zero
    previous = None
    for i in selected:
        best_score = tuple(a + b for a, b in zip(best_score, local_scores[i]))
        if previous is not None and candidates[previous]['trackId'] != candidates[i]['trackId']:
            best_score = (*best_score[:-1], best_score[-1] - 1)
        previous = i
    best_path = [candidates[i] for i in selected]
    bounded = len(candidates) > MAX_PRIMARY_CANDIDATES
    for i, p in enumerate(candidates[:MAX_PRIMARY_CANDIDATES]):
        local = local_scores[i]
        value, parent = local, -1
        for j in range(i):
            edges += 1
            if edges > MAX_PRIMARY_EDGES:
                bounded = True
                break
            previous = candidates[j]
            if previous['end'] > p['start'] + EPS:
                continue
            changed = previous['trackId'] != p['trackId']
            combined = tuple(a + b for a, b in zip(scores[j], local))
            combined = (*combined[:-1], combined[-1] - int(changed))
            if combined > value:
                value, parent = combined, j
        scores.append(value)
        parents.append(parent)
        if value > best_score:
            best_score = value
            path, cursor = [], i
            while cursor >= 0:
                path.append(candidates[cursor])
                cursor = parents[cursor]
            best_path = list(reversed(path))
        if bounded and edges > MAX_PRIMARY_EDGES:
            break
    return best_path, bounded


def backbone(performance, options, main_id, alignment, audio_duration, originals=None):
    from .hybrid_lead import event_rows, union
    from .hybrid_primary import resolve_roles
    from .hybrid_selection import regional_candidates, select_base
    context = performance['compositionContext']
    clock = Clock(context['timeline'])
    tracks = {t['id']: t for t in performance['tracks'] if t['instrument'] == 'guitar'}
    main = tracks[main_id]
    rows = {tid: event_rows(t, context['tracks'][tid], clock) for tid, t in tracks.items()}
    end = clock.quarter(performance['duration'])
    if map_time(alignment, clock.seconds(end), allow_negative=True) > audio_duration:
        low, high = 0.0, end
        for _ in range(48):
            mid = (low + high) / 2
            if map_time(alignment, clock.seconds(mid), allow_negative=True) < audio_duration:
                low = mid
            else:
                high = mid
        end = high
    for tid, values in rows.items():
        for row in values:
            row['available'] = originals is None or _key(row) in originals.get(tid, {}).get('eventIndices', {})
            if row['available'] and row['end'] > end:
                row['end'] = max(row['start'], end)
    roles = resolve_roles(performance, options, main_id)
    limitations = unsupported_regions(performance, clock)
    performance['hybridUnsupportedRegions'] = deepcopy(limitations)
    raw = regional_candidates(performance, options, main_id, rows=rows)
    evidence, seen = [], set()
    for c in sorted(raw, key=lambda c: (c['start'], c['end'], c['trackId'], c['evidence'],
                                      c.get('ownedStart', c['start']), c.get('ownedEnd', c['end']))):
        c = deepcopy(c)
        c['start'], c['end'] = max(0, c['start']), min(end, c['end'])
        c['ownedStart'] = max(0, c.get('ownedStart', c['start']))
        c['ownedEnd'] = min(end, c.get('ownedEnd', c['end']))
        key = c['trackId'], c['start'], c['end'], c['ownedStart'], c['ownedEnd'], c['evidence']
        if key in seen or c['end'] <= c['start'] + EPS or c['ownedEnd'] <= c['ownedStart'] + EPS:
            continue
        seen.add(key)
        c['id'] = 'primary-' + hashlib.sha256(json.dumps(key).encode()).hexdigest()[:16]
        tid = c['trackId']
        if tid in options.get('excludedTrackIds', []):
            c.update(eligible=False, reason='excluded_by_user')
        elif tracks[tid]['tuning'] != main['tuning'] or tracks[tid]['capo'] != main['capo']:
            c.update(eligible=False, reason='incompatible_setup')
        c.setdefault('eligible', True)
        evidence.append(c)
        if not c['eligible']:
            limitations.append({k: c[k] for k in ('trackId', 'start', 'end', 'reason')})
    eligible = [c for c in evidence if c['eligible']]
    candidates_by_events = {}
    requirements = {c['id']: frozenset(_key(r) for r in _members(rows[c['trackId']], clock, c['ownedStart'], c['ownedEnd'])) for c in eligible}
    preferred = options.get('preferredTrackIds', [])
    windows_by_id = {}

    def retain(c, lo, hi, unsupported):
        p = _passage(c, rows[c['trackId']], clock, lo, hi)
        # An unsupported note on another voice cannot veto a safe held solo.
        # Reject surviving fragments of that exact raw gesture, not every
        # independent event whose sounding footprint overlaps its time span.
        if p is None or any(_in_unsupported_gesture(row, bad) for row in p['events'] for bad in unsupported):
            return
        key = (c['trackId'], tuple(_key(r) for r in p['events']))
        old = candidates_by_events.get(key)
        if old is not None:
            if _candidate_score(p, evidence_by_id, main_id, preferred, requirements) <= _candidate_score(
                    old, evidence_by_id, main_id, preferred, requirements):
                return
        candidates_by_events[key] = p

    evidence_by_id = {c['id']: c for c in evidence}
    # Mandatory source windows are constructed before any optional variants.
    # Thus the variant/search cap cannot discard a later complete known solo.
    for c in eligible:
        tid = c['trackId']
        unsupported = [x for x in limitations if x['trackId'] == tid and x['reason'] == 'unsupported_source_gesture']
        holes = [(x['start'], x['end']) for x in unsupported]
        windows = _windows(c['ownedStart'], c['ownedEnd'], holes)
        windows_by_id[c['id']] = windows, unsupported
        retain(c, c['ownedStart'], c['ownedEnd'], unsupported)
        for lo, hi in windows:
            retain(c, lo, hi, unsupported)
    for p in _coalesced(list(candidates_by_events.values()), rows, clock, evidence_by_id, main_id, preferred, requirements):
        key = (p['trackId'], tuple(_key(r) for r in p['events']))
        old = candidates_by_events.get(key)
        if old is None or _candidate_score(p, evidence_by_id, main_id, preferred, requirements) > _candidate_score(
                old, evidence_by_id, main_id, preferred, requirements):
            candidates_by_events[key] = p
    variants_used = overlap_checks = 0
    generation_limited = False
    for c in eligible:
        windows, unsupported = windows_by_id[c['id']]
        # Keep complete candidates first, plus legal local alternatives around
        # competing foreground episodes. A small overlap never erases a whole
        # source island without considering its unique remainder.
        variants, others = set(), []
        for x in eligible:
            overlap_checks += 1
            if overlap_checks > MAX_PRIMARY_EDGES or len(others) >= 64:
                generation_limited = True
                break
            if x['trackId'] != c['trackId'] and overlap((c['start'], c['end']), (x['start'], x['end'])):
                others.append((x['start'], x['end']))
        original_windows = set(windows)
        room = max(0, MAX_PRIMARY_VARIANTS - variants_used)
        for lo, hi in windows:
            for blockers in [others, *[[span] for span in others]]:
                for variant in _windows(lo, hi, blockers):
                    if variant in original_windows or variant in variants:
                        continue
                    if len(variants) >= room:
                        generation_limited = True
                        break
                    variants.add(variant)
                if generation_limited and len(variants) >= room:
                    break
            if generation_limited and len(variants) >= room:
                break
        for lo, hi in sorted(variants):
            variants_used += 1
            retain(c, lo, hi, unsupported)
        if overlap_checks > MAX_PRIMARY_EDGES or variants_used >= MAX_PRIMARY_VARIANTS:
            generation_limited = generation_limited or c is not eligible[-1]
            break
    selected, bounded = _select(list(candidates_by_events.values()), evidence, main_id, preferred, requirements)
    bounded = bounded or generation_limited
    from .hybrid_handover import refine_handovers
    selected, handover_evidence, reservations, handovers = refine_handovers(
        selected, evidence, rows, tracks, context, options, main_id, clock, limitations)
    evidence.extend(handover_evidence)
    bounded = bounded or handovers['budgetLimited']
    regions = [p for p in selected if p['trackId'] != main_id]
    # Original solo reservations remain meaningful around a recovered response.
    # Splitting its physical passages must not automatically revive unrelated
    # base backing in the remaining rests, bypassing the optional-fill search.
    reserved_regions = regions + [p for p in reservations if p['trackId'] != main_id]
    handover_ids = {c['id'] for c in handover_evidence}
    restored_main = {_key(r) for p in selected if p['trackId'] == main_id and p['obligationId'] in handover_ids
                     for r in p['events']}
    kept, removed = [], []
    for r in rows[main_id]:
        if not r['available']:
            continue
        blockers = [p for p in reserved_regions if overlap((r['start'], r['end']), (p['start'], p['end']))]
        if _key(r) in restored_main:
            blockers = []
        if blockers:
            removed.append({**ref(r), 'start': r['start'], 'end': r['end'], 'supersededBy': sorted({p['trackId'] for p in blockers})})
        else:
            kept.append(r)
    main_slots = [(b['start'], min(b['end'], end)) for b in context['tracks'][main_id]['beats'] if not b['rest'] and b['start'] < end
                  and not any(overlap((b['start'], b['end']), (p['start'], p['end'])) for p in reserved_regions)
                  and not any(overlap((b['start'], b['end']), (r['start'], r['end'])) for r in removed)]
    protected = union(main_slots + [(r['start'], r['end']) for r in kept]
                      + [(p['start'], p['end']) for p in [*selected, *reservations]])
    # Only evidenced primary episodes reserve material here. Every optional
    # lead, clean and rhythm phrase competes in the same guarded filler search;
    # a track's name must not give its fragments a cost-free shortcut.
    return {'roles': roles, 'rows': rows, 'mainEvents': [ref(r) for r in kept], 'removedMain': removed,
            'mainProtected': main_slots + [(r['start'], r['end']) for r in kept],
            'passages': sorted(regions, key=lambda p: (p['start'], p['end'], p['trackId'])), 'protected': protected,
            'regionalEvidence': evidence, 'primaryEpisodes': selected, 'primaryReservations': reservations,
            'handovers': handovers, 'limitations': limitations, 'primaryBudgetLimited': bounded,
            'baseSelection': deepcopy(performance.get('hybridBaseSelection') or select_base(performance, {**options, 'mainTrackId': main_id}))}


def finish(result, primary, performance, options, alignment):
    from .hybrid_lead import union
    from .hybrid_selection import parse_track
    clock = Clock(performance['compositionContext']['timeline'])
    at = lambda q: map_time(alignment, clock.seconds(q), allow_negative=True)
    for p in result['passages']:
        p['priority'] = 'accompaniment'
    result['passages'] += primary['passages']
    result['passages'].sort(key=lambda p: (p['start'], p['end'], p['trackId']))
    for p in [*result['passages'], *primary['removedMain'], *primary['primaryReservations']]:
        p['recordingStart'], p['recordingEnd'] = round(at(p['start']), 6), round(at(p['end']), 6)
        if 'ownedStart' in p:
            p['recordingOwnedStart'], p['recordingOwnedEnd'] = round(at(p['ownedStart']), 6), round(at(p['ownedEnd']), 6)
    main_id = result['mainTrackId']
    source_tracks = {track['id']: track for track in performance['tracks']}

    def credible_voice(tid):
        role = primary['roles'].get(tid)
        if role != 'main':
            return role in {'lead', 'solo'}
        requested = options.get('roles', {}).get(tid)
        if requested is not None:
            return requested in {'lead', 'solo'}
        facts = parse_track(source_tracks[tid])
        # 'main' is a structural role, not evidence for or against a parallel
        # lead voice. Recover the actual source's lead/solo intent instead.
        return facts['priorRole'] in {'lead', 'solo'} and (facts['lead'] or facts['solo'])

    chosen = {(p['trackId'], r['kind'], r['index']) for p in result['passages'] for r in p['events']}
    chosen.update((main_id, r['kind'], r['index']) for r in primary['mainEvents'])
    limitations = deepcopy(primary['limitations'])
    obligations = []
    for c in primary['regionalEvidence']:
        tid = c['trackId']
        p = _passage(c, primary['rows'][tid], clock, c['ownedStart'], c['ownedEnd'])
        refs = p['events'] if p else []
        missing = [r for r in refs if (tid, r['kind'], r['index']) not in chosen]
        reason = c.get('reason')
        status = 'included'
        if not c['eligible']:
            status = 'excluded'
        elif missing:
            status = 'limited'
            others = [x for x in primary['primaryEpisodes'] if x['trackId'] != tid and overlap((c['start'], c['end']), (x['start'], x['end']))]
            unsupported = [x for x in limitations if x['trackId'] == tid and x['reason'] == 'unsupported_source_gesture']
            by_key = {_key(row): row for row in primary['rows'][tid]}
            explained = all(any(_in_unsupported_gesture(by_key[_key(r)], x) for x in unsupported) for r in missing)
            reason = 'conflicting_primary' if others else 'unsupported_source_gesture' if explained else 'planning_limit' if primary['primaryBudgetLimited'] else 'unresolved_primary'
            if c['evidence'] != 'named_soloist' and others and credible_voice(tid):
                covered = union((other['start'], other['end']) for other in others
                                if credible_voice(other['trackId']))
                # A short parallel voice can replace the complete first
                # gesture while this source's unique ending remains selected.
                # Every missing whole gesture needs actual lead coverage; a
                # chorus tail merely touching a solo cannot excuse its loss.
                if all(any(a <= by_key[_key(r)]['start'] + EPS and b >= by_key[_key(r)]['end'] - EPS
                           for a, b in covered) for r in missing):
                    reason = 'alternate_voice'
            if reason != 'unsupported_source_gesture':
                limitations.append({'trackId': tid, 'start': c['start'], 'end': c['end'], 'reason': reason})
        footprint = {'start': p['start'], 'end': p['end']} if p else {}
        obligations.append({**{k: c[k] for k in ('id', 'trackId', 'start', 'end', 'evidence', 'confidence')}, **footprint,
                            'events': refs, 'status': status, **({'reason': reason} if reason else {})})
    tracks = {t['id']: t for t in performance['tracks']}
    setup_excluded = []
    for tid, t in tracks.items():
        if t['instrument'] == 'guitar' and tid != main_id:
            if tid in options.get('excludedTrackIds', []):
                setup_excluded.append({'trackId': tid, 'reason': 'excluded_by_user'})
            elif t['tuning'] != tracks[main_id]['tuning'] or t['capo'] != tracks[main_id]['capo']:
                setup_excluded.append({'trackId': tid, 'reason': 'incompatible_setup'})
    excluded = {x['trackId']: x for x in result['excluded']}
    excluded.update({x['trackId']: x for x in setup_excluded})
    contributors = {p['trackId'] for p in result['passages']}
    result['excluded'] = [x for tid, x in sorted(excluded.items()) if tid not in contributors]
    audit = []
    for tid, rows in sorted(primary['rows'].items()):
        for r in rows:
            superseded = []
            if (tid, r['kind'], r['index']) in chosen:
                status, reason = 'included', 'copied_source_event'
            elif not r['available']:
                from .collapsed_opening import omitted
                status = 'source_limitation'
                reason = 'recording_opening' if all(omitted(alignment, n['t']) for n in r['notes']) else 'recording_end'
            elif tid == main_id:
                status, reason = 'superseded', 'regional_primary'
                superseded = sorted({p['trackId'] for p in [*primary['passages'], *primary['primaryReservations']]
                                     if p['trackId'] != main_id and overlap((r['start'], r['end']), (p['start'], p['end']))})
            elif tid in {x['trackId'] for x in setup_excluded}:
                status, reason = 'excluded', excluded[tid]['reason']
            elif primary['roles'][tid] == 'solo' and not any(c['trackId'] == tid and overlap((r['start'], r['end']), (c['start'], c['end'])) for c in primary['regionalEvidence']):
                status, reason = 'non_primary', 'outside_solo_body'
            else:
                status, reason = 'unused_accompaniment', 'phrase_plan_preference'
            audit.append({'trackId': tid, **ref(r), 'start': r['start'], 'end': r['end'],
                          'recordingStart': round(at(r['start']), 6), 'recordingEnd': round(at(r['end']), 6),
                          'status': status, 'reason': reason, 'supersededBy': superseded})
    # Legacy duration fields describe passage spans, not uninterrupted playing.
    main_active = union((r['start'], r['end']) for r in primary['rows'][main_id])
    selected_seconds = added_seconds = 0.0
    for p in result['passages']:
        selected_seconds += at(p['end']) - at(p['start'])
        span = at(p['end']) - at(p['start'])
        taken = sum(at(min(b, p['end'])) - at(max(a, p['start'])) for a, b in main_active if max(a, p['start']) < min(b, p['end']))
        added_seconds += max(0, span - taken)
    limited = any(x['reason'] != 'alternate_voice' for x in limitations)
    result.update(version=3, roles=primary['roles'], mainEvents=primary['mainEvents'], removedMain=primary['removedMain'],
                  coverage={'status': 'limited' if limited else 'complete', 'events': audit},
                  obligations=obligations, limitations=limitations, regionalEvidence=primary['regionalEvidence'],
                  primaryEpisodes=deepcopy(primary['primaryEpisodes']),
                  primaryReservations=deepcopy(primary['primaryReservations']), handovers=deepcopy(primary['handovers']),
                  baseSelection=primary['baseSelection'], primaryBudgetLimited=primary['primaryBudgetLimited'],
                  selectedSeconds=round(selected_seconds, 3), addedSeconds=round(added_seconds, 3),
                  status='created' if result['passages'] else 'no_additions')
    return result
