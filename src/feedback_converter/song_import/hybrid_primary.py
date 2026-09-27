"""Construct the primary lead before optional accompaniment is considered.

The receipt accounts for every performed chart event. This is production
selection; verify_hybrid_priority independently audits its coverage and priority.
"""
from copy import deepcopy
import re

from .audio import ImportFailure

EPS = 1e-7


def suggested_role(track):
    name = track['name']
    if re.search(r'\b(delay|echo|effect|fx)\b', name, re.I):
        return 'accompaniment'
    if re.search(r'\bsolo\b', name, re.I):
        return None if re.search(r'\b(chords?|rhythm|harmon(?:y|ies))\b', name, re.I) else 'solo'
    if re.search(r'\blead\b', name, re.I):
        return 'lead'
    if re.search(r'\b(clean|rhythm|acoustic|classical|background|extra|overdubs?)\b', name, re.I):
        return 'accompaniment'
    return None


def review(performance, options, main_id, message, **details):
    raise ImportFailure('awaiting_main_choice', message, {
        'sourceSha256': options.get('sourceSha256'), 'suggestedMainTrackId': main_id,
        'suggestedRoles': {t['id']: suggested_role(t) or '' for t in performance['tracks'] if t['instrument'] == 'guitar'},
        'tracks': [{**{k: t[k] for k in ('id', 'name', 'role', 'tuning', 'capo')}, 'playable': bool(t['notes'] or t['chords'])}
                   for t in performance['tracks'] if t['instrument'] == 'guitar'
                   and (t['notes'] or t['chords'] or t['id'] in performance.get('hybridOmittedTracks', []))],
        **details})


def resolve_roles(performance, options, main_id):
    roles = {}
    for track in performance['tracks']:
        tid = track['id']
        if track['instrument'] != 'guitar':
            continue
        if tid == main_id:
            roles[tid] = 'main'
        elif tid in options.get('excludedTrackIds', []):
            roles[tid] = 'excluded'
        elif not track['notes'] and not track['chords'] and tid not in performance.get('hybridOmittedTracks', []):
            roles[tid] = options.get('roles',{}).get(tid, suggested_role(track) or 'accompaniment')
        else:
            role = options.get('roles', {}).get(tid, suggested_role(track))
            if role is None:
                review(performance, options, main_id, 'Confirm which guitars carry the lead or solo before creating Hybrid Lead.', ambiguousTrackId=tid)
            roles[tid] = role
    return dict(sorted(roles.items()))


def _ref(row):
    return {k: deepcopy(row[k]) for k in ('kind', 'index', 'sourceIds', 'occurrences')}


def _overlap(a, b):
    # Zero-length attacks still occupy their exact onset.
    return a[0] < b[1] - EPS and a[1] > b[0] + EPS or a[0] == a[1] and b[0] - EPS <= a[0] < b[1] - EPS


def _islands(rows):
    from .hybrid_lead import union
    islands = []
    for a, b in union((r['start'], r['end']) for r in rows):
        if islands and a - islands[-1][1] < 1 - EPS:
            islands[-1][1] = b
        else:
            islands.append([a, b])
    return islands


def solo_regions(rows, sections):
    """Use authored solo entries and the complete non-ghost body of each island.

Ghost-only tails after the final primary gesture do not reserve the rest of
the song. Their disposition remains visible and independently audited.
"""
    result = []
    for lo, hi in _islands(rows):
        local = [r for r in rows if lo - EPS <= r['start'] and r['end'] <= hi + EPS]
        core = [r for r in local if any(not n.get('ghost') and not n.get('mt') for n in r['notes'])]
        if not core:
            continue
        entries = [s for s in sections if lo - EPS <= s <= core[-1]['end'] + EPS]
        # Section labels support the role; they never erase a non-ghost pickup
        # from the dedicated solo source just before the labelled measure.
        start, end = min(r['start'] for r in core), max(r['end'] for r in core)
        # The body can include a connected ghost endpoint. Expand to complete
        # gestures, never crop them at either the label or ghost transition.
        while True:
            touched = [r for r in local if _overlap((r['start'], r['end']), (start, end))]
            bounds = min([start] + [r['start'] for r in touched]), max([end] + [r['end'] for r in touched])
            if bounds == (start, end):
                break
            start, end = bounds
        result.append({'start': start, 'end': end, 'evidence': 'authored_solo_and_complete_body' if entries else 'dedicated_solo_body'})
    return result


def backbone(performance, options, main_id, alignment, audio_duration, originals=None):
    from .hybrid_lead import event_rows, union
    from .hybrid_context import Clock
    from .alignment import map_time
    context = performance['compositionContext']
    clock = Clock(context['timeline'])
    roles = resolve_roles(performance, options, main_id)
    tracks = {t['id']: t for t in performance['tracks']}
    main = tracks[main_id]
    rows = {tid: event_rows(t, context['tracks'][tid], clock) for tid, t in tracks.items() if t['instrument'] == 'guitar'}
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
    # Only the already-prepared originals authorize endpoint normalization.
    for tid, values in rows.items():
        for row in values:
            row['available'] = originals is None or (row['kind'], row['index']) in originals[tid]['eventIndices']
            if row['available'] and row['end'] > end:
                row['end'] = max(row['start'], end)
    preferred = options.get('preferredTrackIds', [])
    order = sorted(roles, key=lambda tid: (0 if roles[tid] == 'solo' else 1, preferred.index(tid) if tid in preferred else len(preferred), tid))
    solo_starts = [clock.quarter(s['time']) for s in performance.get('sections', []) if re.search(r'\bsolo\b', s['name'], re.I)]
    regions, removed = [], []
    for tid in order:
        if roles[tid] != 'solo':
            continue
        if tracks[tid]['tuning'] != main['tuning'] or tracks[tid]['capo'] != main['capo']:
            review(performance, options, main_id, 'A primary solo has incompatible tuning or capo. Resolve its role or exclude it explicitly.', conflictTrackId=tid)
        if tid in performance.get('hybridOmittedTracks', []):
            review(performance, options, main_id, 'A primary solo contains unsupported source events. Resolve this before completing Hybrid Lead.', conflictTrackId=tid)
        for region in solo_regions([r for r in rows[tid] if r['available']], solo_starts):
            blockers = [p for p in regions if _overlap((region['start'], region['end']), (p['start'], p['end']))]
            if blockers:
                if tid not in preferred and any(p['trackId'] not in preferred for p in blockers):
                    review(performance, options, main_id, 'Two primary solos overlap. Set their priority or revise their roles.', conflictTrackIds=[tid, *[p['trackId'] for p in blockers]])
                continue
            selected = [r for r in rows[tid] if r['available'] and region['start'] - EPS <= r['start'] and r['end'] <= region['end'] + EPS and r['start'] < region['end'] - EPS]
            if not selected:
                continue
            regions.append({**region, 'trackId': tid, 'priority': 'solo', 'events': [_ref(r) for r in selected]})
    main_rows = rows[main_id]
    kept = []
    for row in main_rows:
        if not row['available']:
            continue
        blockers = [p for p in regions if _overlap((row['start'], row['end']), (p['start'], p['end']))]
        if blockers:
            removed.append({**_ref(row), 'start': row['start'], 'end': row['end'], 'supersededBy': sorted({p['trackId'] for p in blockers})})
        elif row['available']:
            kept.append(row)
    main_slots = [(b['start'], min(b['end'], end)) for b in context['tracks'][main_id]['beats'] if not b['rest'] and b['start'] < end
                  and not any(_overlap((b['start'], b['end']), (p['start'], p['end'])) for p in regions)
                  and not any(_overlap((b['start'], b['end']), (p['start'], p['end'])) for p in removed)]
    protected = union(main_slots + [(r['start'], r['end']) for r in kept] + [(p['start'], p['end']) for p in regions])
    for tid in order:
        if roles[tid] != 'lead':
            continue
        if tracks[tid]['tuning'] != main['tuning'] or tracks[tid]['capo'] != main['capo'] or tid in performance.get('hybridOmittedTracks', []):
            review(performance, options, main_id, 'A primary lead cannot be represented with this guitar setup. Resolve its role or exclude it explicitly.', conflictTrackId=tid)
        # Primary lead gestures fill primary gaps without optional comfort
        # padding. Higher-priority primary gestures remain authoritative.
        groups = {}
        for row in rows[tid]:
            groups.setdefault((row['start'], row['end']), []).append(row)
        for (lo, hi), members in sorted(groups.items()):
            peers = [p for p in regions if p['priority'] == 'lead' and p['trackId'] != tid
                     and _overlap((lo, hi), (p['start'], p['end']))]
            if peers and tid not in preferred and any(p['trackId'] not in preferred for p in peers):
                review(performance, options, main_id, 'Two additional leads overlap. Set their priority or revise their roles.',
                       conflictTrackIds=[tid, *sorted({p['trackId'] for p in peers})])
            if any(not r['available'] for r in members) or any(_overlap((lo, hi), p) for p in protected):
                continue
            region = {'trackId': tid, 'priority': 'lead', 'start': lo, 'end': hi, 'evidence': 'complete_lead_gesture', 'events': [_ref(r) for r in members]}
            regions.append(region)
            protected = union(protected + [[lo, hi]])
    for p in regions:
        p.update(boundaries=['primary', 'primary'], boundaryQuarters=[p['start'], p['end']], entryFret=None, exitFret=None)
    return {'roles': roles, 'rows': rows, 'mainEvents': [_ref(r) for r in kept], 'removedMain': removed,
            'mainProtected': main_slots + [(r['start'],r['end']) for r in kept],
            'passages': sorted(regions, key=lambda p: (p['start'], p['end'], p['trackId'])), 'protected': protected}


def finish(result, primary, performance, options, alignment):
    from .alignment import map_time
    from .hybrid_context import Clock
    clock = Clock(performance['compositionContext']['timeline'])
    for p in result['passages']:
        p['priority'] = 'accompaniment'
    result['passages'] += primary['passages']
    result['passages'].sort(key=lambda p: (p['start'], p['end'], p['trackId']))
    for p in result['passages']:
        p['recordingStart'] = round(map_time(alignment, clock.seconds(p['start']), allow_negative=True), 6)
        p['recordingEnd'] = round(map_time(alignment, clock.seconds(p['end']), allow_negative=True), 6)
    for row in primary['removedMain']:
        row['recordingStart'] = round(map_time(alignment, clock.seconds(row['start']), allow_negative=True), 6)
        row['recordingEnd'] = round(map_time(alignment, clock.seconds(row['end']), allow_negative=True), 6)
    selected = {(p['trackId'], e['kind'], e['index']) for p in result['passages'] for e in p['events']}
    selected.update((result['mainTrackId'], e['kind'], e['index']) for e in primary['mainEvents'])
    excluded = {r['trackId']: r['reason'] for r in result['excluded']}
    decisions = {}
    for candidate in result.get('candidateDecisions', []):
        for ref in candidate['events']:
            decisions.setdefault((candidate['trackId'],ref['kind'],ref['index']), set()).add(candidate['reason'])
    audit = []
    for tid, rows in sorted(primary['rows'].items()):
        for r in rows:
            key = tid, r['kind'], r['index']
            superseded_by = []
            if key in selected:
                status, reason = 'included', 'copied_source_event'
            elif not r['available']:
                status, reason = 'source_limitation', 'recording_end'
            elif tid == result['mainTrackId']:
                status, reason = 'superseded', 'primary_solo'
                superseded_by = sorted({p['trackId'] for p in primary['passages'] if p['priority'] == 'solo'
                                        and _overlap((r['start'], r['end']), (p['start'], p['end']))})
            elif primary['roles'][tid] == 'excluded':
                status, reason = 'excluded', 'explicit_user_choice'
            elif primary['roles'][tid] in {'solo', 'lead'}:
                blockers = [p for p in primary['passages'] if p['trackId'] != tid
                            and _overlap((r['start'], r['end']), (p['start'], p['end']))]
                superseded_by = sorted({p['trackId'] for p in blockers})
                if primary['roles'][tid] == 'lead':
                    if any(_overlap((r['start'],r['end']), span) for span in primary['mainProtected']):
                        superseded_by = sorted(set(superseded_by + [result['mainTrackId']]))
                    status, reason = 'superseded', 'higher_primary'
                else:
                    # Explicitly preferred parallel solos displace whole islands,
                    # including portions extending beyond the overlap itself.
                    for a,b in _islands(rows):
                        if a-EPS <= r['start'] and r['end'] <= b+EPS:
                            superseded_by = sorted({p['trackId'] for p in primary['passages'] if p['priority'] == 'solo'
                                                    and p['trackId'] != tid and _overlap((a,b), (p['start'],p['end']))})
                    status, reason = ('superseded', 'preferred_solo') if superseded_by else ('non_primary', 'outside_solo_body')
            else:
                specific = decisions.get(key)
                status = 'unused_accompaniment'
                reason = 'phrase_plan_preference' if specific and 'eligible_phrase' in specific else ','.join(sorted(specific)) if specific else excluded.get(tid, 'no_supported_phrase_boundary')
            audit.append({'trackId': tid, **_ref(r), 'start': r['start'], 'end': r['end'],
                          'recordingStart': round(map_time(alignment,clock.seconds(r['start']),allow_negative=True),6),
                          'recordingEnd': round(map_time(alignment,clock.seconds(r['end']),allow_negative=True),6),
                          'status': status, 'reason': reason, 'supersededBy': superseded_by})
    from .hybrid_lead import union
    main_active = union([(r['start'],r['end']) for r in primary['rows'][result['mainTrackId']]])
    selected_seconds = added_seconds = 0.0
    for p in result['passages']:
        span = p['recordingEnd'] - p['recordingStart']
        selected_seconds += span
        overlap = sum(map_time(alignment,clock.seconds(min(b,p['end'])),allow_negative=True) - map_time(alignment,clock.seconds(max(a,p['start'])),allow_negative=True)
                      for a,b in main_active if max(a,p['start']) < min(b,p['end']))
        added_seconds += max(0,span-overlap)
    result.update(version=2, roles=primary['roles'], mainEvents=primary['mainEvents'], removedMain=primary['removedMain'],
                  coverage={'status': 'complete', 'events': audit},
                  selectedSeconds=round(selected_seconds,3), addedSeconds=round(added_seconds,3),
                  status='created' if result['passages'] else 'no_additions')
    return result
