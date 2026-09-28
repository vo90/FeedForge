"""Recover credible peer lead gestures inside an already chosen solo's rests.

The incumbent's complete event set is immutable. This refinement adds source
references only; original reservations keep unrelated backing out of its rests.
"""
from copy import deepcopy
from hashlib import sha256
import json

from .hybrid_selection import (_backing_context, _canonical, _islands, _local,
                               _musical_score, _named_people, parse_track)

EPS = 1e-7
MAX_HANDOVER_WINDOWS = 1024
MAX_HANDOVER_CANDIDATES = 4096
MAX_HANDOVER_GROUP_CHECKS = 250000


def _key(row):
    return row['kind'], row['index']


def _pitched(row):
    return [n for n in row['notes'] if not n.get('ghost') and not n.get('mt')
            and 0 <= n.get('f', -1) <= 24]


def _named_peer(facts, parent):
    return any(person <= set(facts['performerTokens'])
               for person in _named_people(parent.get('sectionName', '')))


def _unknown_line(data):
    return bool(data and data['attacks'] >= 4 and data['single'] >= .5 and data['variety'] >= 4
                and data['expressive'] >= .15 and data['motifRepeat'] < .5
                and max(data['palmMute'], data['letRing']) < .35)


def _unsupported_slot(beat, unsupported):
    """Only a fully evidenced, performed raw-gesture omission frees a slot."""
    notes = beat.get('noteIds', [])
    return bool(notes) and all(any(ident in bad.get('sourceIds', [])
                                   and beat.get('occurrence') in bad.get('occurrences', [])
                                   and bad['start'] <= beat['start'] + EPS
                                   and bad['end'] >= beat['end'] - EPS
                                   for bad in unsupported) for ident in notes)


def _featured(candidate):
    return (candidate.get('priority') == 'solo' or candidate.get('labelledSolo')
            or candidate['evidence'] in {'named_soloist', 'dedicated_solo'})


def _unsupported_windows(selected, evidence, rows, written, unsupported, main_id):
    """Inspect omissions between selected pieces of the same featured owner.

    These are processing windows, never empty episodes or reservations. Existing
    episodes and retained base material remain immutable occupied boundaries.
    """
    from .hybrid_lead import union
    from .hybrid_regional import _windows, overlap
    if not selected or not any(unsupported[tid] for tid in written):
        return []
    selected_ids = {p['obligationId'] for p in selected}
    foreign = [(p['start'], p['end']) for p in selected if p['trackId'] != main_id]
    occupied = [(p['start'], p['end']) for p in selected]
    occupied.extend((r['start'], r['end']) for r in rows[main_id] if r['available']
                    and not any(overlap((r['start'], r['end']), span) for span in foreign))
    windows = []
    for parent in sorted(evidence, key=lambda p: (p['start'], p['end'], p['trackId'], p['id'])):
        if parent['id'] not in selected_ids or not parent.get('eligible', True) or not _featured(parent):
            continue
        owner = parent['trackId']
        bad = unsupported[owner]
        if not bad:
            continue
        blocked = occupied + [(b['start'], b['end']) for b in written[owner]]
        for a, b in union((p['start'], p['end']) for p in bad):
            lo, hi = max(a, parent.get('ownedStart', parent['start'])), min(b, parent.get('ownedEnd', parent['end']))
            for start, end in _windows(lo, hi, blocked):
                if end - start < 1 - EPS:
                    continue
                windows.append({**parent, 'obligationId': parent['id'], 'start': start, 'end': end,
                                'ownedStart': start, 'ownedEnd': end, 'events': [], '_unsupportedFallback': True})
                # The normal handover loop records the shared budget limit.
                if len(windows) > MAX_HANDOVER_WINDOWS:
                    return windows
    return windows


def _credible(track, facts, parent, rows, lo, hi, clock, options):
    requested = options.get('roles', {}).get(track['id'])
    if requested == 'accompaniment':
        return False
    if requested in {'lead', 'solo'}:
        return True
    if facts['effect'] or facts['harmony'] or facts['extra'] or facts['bassRegisterLayer'] or facts['octaveTextureLayer']:
        return False
    context = _local(track, rows, parent.get('ownedStart', parent['start']),
                     parent.get('ownedEnd', parent['end']), clock)
    if not context or _backing_context(context):
        return False
    if facts['lead'] or facts['dedicatedSolo']:
        return True
    # A named player's actual solo may live on their rhythm/clean arrangement.
    # Identity alone cannot turn repeating accompaniment into foreground.
    local = _local(track, rows, lo, hi, clock)
    if _named_peer(facts, parent) and local and local['expressive'] >= .12 and (local['variety'] >= 3 or local['rhythmVariety'] >= .3):
        return True
    # Unlabelled fills can answer a solo without naming either guitarist in
    # the section. Demand independent parent and local melodic evidence; a
    # busy rhythm part or an isolated ornament is not enough to infer a lead.
    if facts['priorRole'] != 'unknown' or not local:
        return False
    expressive_attacks = {round(clock.quarter(n['t']), 8) for r in rows for n in _pitched(r)
                          if lo - EPS <= clock.quarter(n['t']) < hi - EPS
                          and (n.get('bn') or n.get('bnv') or n.get('vb') or n.get('hp')
                               or n.get('ho') or n.get('po') or n.get('sl') is not None or n.get('slu') is not None)}
    return (context['single'] >= .5 and context['expressive'] >= .12
            and _unknown_line(local) and len(expressive_attacks) >= 2)


def refine_handovers(selected, evidence, rows, tracks, context, options, main_id, clock, limitations):
    """Return physical episodes, extra evidence, original reservations and audit.

    Deliberate primary rests remain reserved unless a meaningful peer response
    fits in full. No optional-fill duration floor applies to a real solo answer.
    Caps preserve every original owner reference and any completed refinement.
    """
    from .hybrid_regional import _in_unsupported_gesture, _members, _passage, _windows, overlap
    by_id = {c['id']: c for c in evidence}
    facts = {tid: parse_track(t) for tid, t in tracks.items()}
    main = tracks[main_id]
    excluded = set(options.get('excludedTrackIds', []))
    peers = [tid for tid in tracks if tid not in excluded
             and tracks[tid]['tuning'] == main['tuning'] and tracks[tid].get('capo', 0) == main.get('capo', 0)]
    peers.sort(key=lambda tid: _canonical(tracks[tid]))
    grouped = {}
    for tid, values in rows.items():
        groups = {}
        for row in values:
            groups.setdefault((row['start'], row['end']), []).append(row)
        grouped[tid] = sorted(groups.items())
    unsupported = {tid: [x for x in limitations if x['trackId'] == tid
                        and x['reason'] == 'unsupported_source_gesture'] for tid in tracks}
    # Prove a source's written slots once, not once per selected parent/piece.
    written = {tid: [b for b in context['tracks'][tid]['beats']
                     if not b['rest'] and not _unsupported_slot(b, unsupported[tid])]
               for tid in {p['trackId'] for p in selected}}
    refined, added, reservations, decisions = [], [], [], []
    windows_seen = checks = candidates_seen = 0
    limit_reason = None
    unsupported_windows = _unsupported_windows(selected, evidence, rows, written, unsupported, main_id)
    for original in [*selected, *unsupported_windows]:
        parent = by_id[original['obligationId']]
        fallback = original.get('_unsupportedFallback', False)
        if not _featured(parent) or limit_reason:
            if not fallback:
                refined.append(original)
            continue
        owner = original['trackId']
        original_keys = {_key(r) for r in original['events']}
        owner_rows = [r for r in rows[owner] if _key(r) in original_keys]
        occupied = [(r['start'], r['end']) for r in owner_rows]
        occupied.extend((b['start'], b['end']) for b in written[owner]
                        if overlap((b['start'], b['end']),
                            (original['ownedStart'], original['ownedEnd'])))
        if fallback:
            occupied.extend((p['start'], p['end']) for p in refined)
        holes = _windows(original['start'], original['end'], occupied)
        insertions = []
        for lo, hi in holes:
            if hi - lo < 1 - EPS:
                continue
            windows_seen += 1
            if windows_seen > MAX_HANDOVER_WINDOWS:
                limit_reason = 'handover_window_budget'
                break
            substitution = fallback or any(overlap((lo, hi), (bad['start'], bad['end'])) for bad in unsupported[owner])
            candidates = []
            for tid in peers:
                if tid == owner or not _credible(tracks[tid], facts[tid], parent, rows[tid], lo, hi, clock, options):
                    continue
                if substitution and options.get('roles', {}).get(tid) not in {'lead', 'solo'} and not (
                        facts[tid]['lead'] or facts[tid]['dedicatedSolo']):
                    continue
                inferred_unknown = (facts[tid]['priorRole'] == 'unknown'
                                    and options.get('roles', {}).get(tid) not in {'lead', 'solo'}
                                    and not _named_peer(facts[tid], parent))
                contained, blockers = [], []
                for (a, b), members in grouped[tid]:
                    if a >= hi - EPS:
                        break
                    if b <= lo + EPS:
                        continue
                    checks += 1
                    if checks > MAX_HANDOVER_GROUP_CHECKS:
                        limit_reason = 'handover_group_budget'
                        break
                    usable = (a >= lo - EPS and b <= hi + EPS and all(r['available'] for r in members)
                              and not any(_in_unsupported_gesture(r, bad) for r in members for bad in unsupported[tid]))
                    if usable:
                        contained.extend(members)
                    else:
                        blockers.append((a, b))
                if limit_reason:
                    break
                for a, b in _windows(lo, hi, blockers):
                    local_rows = [r for r in contained if r['start'] >= a - EPS and r['end'] <= b + EPS]
                    for start, end in _islands(local_rows):
                        members = _members(rows[tid], clock, start, end)
                        if not members or any(r not in local_rows for r in members):
                            continue
                        pitched = [n for r in members for n in _pitched(r)]
                        attacks = {round(clock.quarter(n['t']), 8) for n in pitched}
                        data = _local(tracks[tid], members, start, end, clock)
                        held = len(attacks) == 1 and end - start >= 1 - EPS and data and data['expressive'] > 0
                        if not data or _backing_context(data) or (len(attacks) < 2 and not held):
                            continue
                        # Separate weak islands must not borrow each other's
                        # attacks/expression to qualify an unlabelled source.
                        if inferred_unknown and (len(attacks) < 4 or not _unknown_line(data)):
                            continue
                        candidates_seen += 1
                        if candidates_seen > MAX_HANDOVER_CANDIDATES:
                            limit_reason = 'handover_candidate_budget'
                            break
                        candidates.append({'trackId': tid, 'start': start, 'end': end, 'rows': members,
                                           'data': data, 'holeStart': lo, 'holeEnd': hi,
                                           'unsupportedOwnerFallback': substitution})
                    if limit_reason:
                        break
                if limit_reason:
                    break
            # Local content ranks complete responses. Duration is a last tie
            # breaker, never a reward for density or a high string register.
            preferred = options.get('preferredTrackIds', [])
            def rank(candidate):
                tid = candidate['trackId']
                return (-round(_musical_score(candidate['data']), 6),
                        preferred.index(tid) if tid in preferred else len(preferred),
                        -int(tid == main_id), -(candidate['end'] - candidate['start']), _canonical(tracks[tid]))
            accepted = []
            for candidate in sorted(candidates, key=rank):
                if not any(overlap((candidate['start'], candidate['end']), (p['start'], p['end'])) for p in accepted):
                    accepted.append(candidate)
            insertions.extend(accepted)
            decisions.append({'parentObligationId': original['obligationId'], 'ownerTrackId': owner,
                              'start': lo, 'end': hi, 'candidateCount': len(candidates),
                              'selected': [{k: p[k] for k in ('trackId', 'start', 'end')} for p in sorted(accepted, key=lambda p: p['start'])],
                              'reason': 'complete_peer_response' if accepted else 'no_credible_complete_peer_response',
                              **({'unsupportedOwnerFallback': True} if substitution else {})})
            if limit_reason:
                break
        if not insertions:
            if not fallback:
                refined.append(original)
            continue
        pieces = []
        for lo, hi in _windows(original['ownedStart'], original['ownedEnd'],
                               [(p['start'], p['end']) for p in insertions]):
            piece = _passage(parent, owner_rows, clock, lo, hi)
            if piece:
                pieces.append({**deepcopy(original), **piece})
        # Defensive invariant: a new peer never costs even one owner event.
        if {_key(r) for p in pieces for r in p['events']} != original_keys:
            refined.append(original)
            decisions.append({'parentObligationId': original['obligationId'], 'ownerTrackId': owner,
                              'reason': 'owner_preservation_guard', 'selected': []})
            continue
        if not fallback:
            reservations.append(deepcopy(original))
        refined.extend(pieces)
        for insertion in sorted(insertions, key=lambda p: (p['start'], p['end'], p['trackId'])):
            tid, lo, hi = insertion['trackId'], insertion['start'], insertion['end']
            identity = [original['obligationId'], tid, lo, hi, 'local_lead_handover']
            c = {'id': 'primary-' + sha256(json.dumps(identity).encode()).hexdigest()[:16],
                 'trackId': tid, 'start': lo, 'end': hi, 'ownedStart': lo, 'ownedEnd': hi,
                 'priority': 'solo', 'evidence': 'regional_lead', 'confidence': 'medium',
                 'eligible': True, 'score': _musical_score(insertion['data']),
                 'sectionName': parent.get('sectionName', ''), 'labelledSolo': bool(parent.get('labelledSolo')),
                 'selectionEvidence': {'reason': 'local_lead_handover', 'parentObligationId': original['obligationId'],
                     'ownerTrackId': owner, 'holeStart': insertion['holeStart'], 'holeEnd': insertion['holeEnd'],
                     'musicalScore': round(_musical_score(insertion['data']), 6),
                     **({'unsupportedOwnerFallback': True} if insertion['unsupportedOwnerFallback'] else {})}}
            p = _passage(c, rows[tid], clock, lo, hi)
            added.append(c)
            refined.append(p)
    audit = {'algorithm': 'complete-peer-response-v1', 'budgetLimited': bool(limit_reason),
             'reason': limit_reason, 'windows': min(windows_seen, MAX_HANDOVER_WINDOWS),
             'candidateCount': min(candidates_seen, MAX_HANDOVER_CANDIDATES),
             'groupChecks': min(checks, MAX_HANDOVER_GROUP_CHECKS), 'decisions': decisions}
    return sorted(refined, key=lambda p: (p['start'], p['end'], p['trackId'])), added, reservations, audit
