"""Independent, bounded source opportunities in actual Hybrid Lead rests.

This module receives independently reconstructed written/technique facts. It
does not call the producer's planner, role classifier, scoring or diagnostics.
Parallel voices are alternatives; another guitarist's complete gesture in a
selected voice's real rest is a different question from harmony preference.
"""
from bisect import bisect_left, bisect_right
from collections import defaultdict
import re

EPS = 1e-5
MAX_REPORTED_OPPORTUNITIES = 200
MAX_GROUP_CHECKS = 100000


def joined(spans):
    result = []
    for start, end in sorted(spans, key=lambda span: (span[0], span[1])):
        if end <= start + EPS:
            continue
        if result and start <= result[-1][1] + EPS:
            result[-1][1] = max(result[-1][1], end)
        else:
            result.append([start, end])
    return result


def rests(start, end, occupied):
    cursor = start
    for left, right in joined(occupied):
        if right <= cursor + EPS or left >= end - EPS:
            continue
        if left > cursor + EPS:
            yield cursor, min(left, end)
        cursor = max(cursor, right)
    if end > cursor + EPS:
        yield cursor, end


def pitched(note):
    return not note.get('ghost') and not note.get('mt') and note.get('f', -1) in range(25)


def groups(values):
    grouped = defaultdict(dict)
    for key, row in values.items():
        grouped[(round(row['start'], 5), round(row['end'], 5))][key] = row
    return [(start, end, members) for (start, end), members in sorted(grouped.items())]


def contained(index, start, end, budget):
    starts, entries = index
    cursor = bisect_left(starts, start - EPS)
    for position in range(cursor, len(entries)):
        left, right, members = entries[position]
        if left >= end - EPS:
            break
        if budget['checks'] >= budget['maximum']:
            budget['limited'] = True
            break
        budget['checks'] += 1
        if left >= start - EPS and right <= end + EPS:
            yield left, right, members


def _meaningful(members):
    attacks = {round(row['onset'], 5) for row in members.values() if any(pitched(n) for n in row['notes'])}
    expressive = any(row['end'] - row['start'] >= 1 - EPS and
                     any(pitched(n) and any(n.get(k) for k in ('bn', 'vb', 'whammy', 'sl', 'slu'))
                         for n in row['notes']) for row in members.values())
    return len(attacks) >= 2 or expressive


def _expressive_response(members, *, local, tuning=(), require_expression=True):
    """Categorical corroboration for an otherwise unlabelled foreground line.

    This is not a ranking score. Plain ostinatos, recurrent chordal backing and
    a track name alone cannot establish an additional compulsory solo answer.
    Already selected plain melodic alternatives need no expressive markings;
    only a newly compulsory unknown-source answer requires that corroboration.
    """
    attacks = defaultdict(list)
    for row in members.values():
        attacks[round(row['onset'], 5)].extend(n for n in row['notes'] if pitched(n))
    attacks = {time: notes for time, notes in attacks.items() if notes}
    notes = [n for values in attacks.values() for n in values]
    def pitch(note):
        return tuning[note['s']] + note['f'] if 0 <= note['s'] < len(tuning) else (note['s'], note['f'])
    if len(attacks) < 4 or len({pitch(n) for n in notes}) < 4:
        return False
    def expressive_note(note):
        return (any(note.get(k) for k in ('bn', 'bnv', 'vb', 'ho', 'po', 'hp'))
                or any(note.get(k) is not None for k in ('sl', 'slu')))
    expressive = sum(any(expressive_note(n) for n in values) for values in attacks.values())
    if (sum(len(values) == 1 for values in attacks.values()) * 2 < len(attacks)
            or sum(bool(n.get('pm') or n.get('lr')) for n in notes) / len(notes) >= .35):
        return False
    if require_expression and (expressive < 2 or sum(expressive_note(n) for n in notes) / len(notes) < .2):
        return False
    if local:
        signatures = [tuple(sorted(pitch(n) for n in attacks[time])) for time in sorted(attacks)]
        motifs = defaultdict(list)
        for i in range(len(signatures)-2):
            motifs[tuple(signatures[i:i+3])].append(i)
        if any(positions[-1] - positions[0] >= 3 for positions in motifs.values()):
            return False  # Repeated cells are ambiguous, even with ornaments.
    return True


def _complete_responses(index, start, end, budget, supported=None):
    """Separate local answers and exclude overlapping uncopyable gestures."""
    complete, blocked = [], []
    for left, right, members in index[1]:
        if left >= end - EPS:
            break
        if budget['checks'] >= budget['maximum']:
            budget['limited'] = True
            return
        budget['checks'] += 1
        if right <= start + EPS:
            continue
        if left >= start - EPS and right <= end + EPS and (supported is None or members.keys() <= supported):
            complete.append((left, right, members))
        else:
            blocked.append((left, right))
    for lo, hi in rests(start, end, blocked):
        batch, last = {}, lo
        for left, right, members in complete:
            if left < lo-EPS or right > hi+EPS:
                continue
            if batch and left - last >= 1-EPS:
                yield batch
                batch = {}
            batch.update(members)
            last = max(last, right)
        if batch:
            yield batch


def local_lead_audit(rows, requirements, selected, available, recording_at, *, peer_eligible=None,
                     featured_sections=(), featured_sources=(), unknown_peers=(),
                     source_tunings=None, incumbent_eligible=None,
                     unsupported_spans=None, fallback_sources=(), supported_rows=None, quarter_at=None,
                     max_group_checks=MAX_GROUP_CHECKS):
    """Named solos and strongly evidenced unlabelled answers retain responses.

    Only complete hard gestures entirely inside selected credible lead rests
    count. A crossing sustain, unsupported setup, user exclusion or tiny noise
    event cannot become a mandatory repair. Occupancy of backing sources does
    not excuse a missing named lead. Generic unknown-source obligations are
    narrower: an independently credible incumbent must exist, and the response
    must fit an actual remaining chart rest without replacing selected material.
    Simultaneous unknown-source preferences remain outside this oracle.
    Proven unsupported owner gestures are a separate narrow fallback case:
    complete expressive known-lead alternatives may fill the resulting physical
    silence, without demanding the impossible original or replacing playable
    owner material. Unsupported spans come only from the independent raw source.
    """
    indices = {tid: ([a for a, _, _ in entries], entries)
               for tid, values in rows.items() for entries in [groups(values)]}
    supported_rows = rows if supported_rows is None else supported_rows
    unresolved = []
    seen = set()
    budget = {'checks': 0, 'maximum': max_group_checks, 'limited': False}
    physical = joined((row['start'], row['end']) for tid, values in rows.items() for key, row in values.items()
                      if (tid, *key) in selected)
    physical_starts, physical_ends = [a for a, _ in physical], [b for _, b in physical]

    def contexts():
        for requirement in requirements:
            if requirement['evidence'] == 'named_soloist' and len(requirement['owners']) >= 2:
                yield requirement
        # Budgeted source scans also cover this additional inference; limiting
        # only the displayed opportunities would not bound the actual work.
        for section in featured_sections:
            owners, peers = {}, set()
            for tid in sorted(set(featured_sources) & available):
                members = {key: row for _, _, component in contained(
                    indices[tid], section['start'], section['end'], budget) for key, row in component.items()}
                tuning = (source_tunings or {}).get(tid, ())
                if ((incumbent_eligible is not None and incumbent_eligible(tid, section['start'], section['end']))
                        or _expressive_response(members, local=False, tuning=tuning, require_expression=False)):
                    owners[tid] = set(members)
                if tid in unknown_peers and _expressive_response(members, local=False, tuning=tuning):
                    peers.add(tid)
                if budget['limited']:
                    return
            if peers:
                yield {**section, 'evidence': 'expressive_response', 'owners': owners, 'responsePeers': peers}
            for tid, members in owners.items():
                context = {key: rows[tid][key] for key in members}
                credible = ((incumbent_eligible is not None and incumbent_eligible(tid, section['start'], section['end']))
                            or _expressive_response(context, local=False, tuning=(source_tunings or {}).get(tid, ())))
                if not credible or not any((tid, *key) in selected for key in members):
                    continue
                for start, end in (unsupported_spans or {}).get(tid, ()):
                    if budget['checks'] >= budget['maximum']:
                        budget['limited'] = True
                        return
                    budget['checks'] += 1
                    start, end = max(start, section['start']), min(end, section['end'])
                    if end > start + EPS:
                        yield {**section, 'start': start, 'end': end,
                               'evidence': 'unsupported_lead_fallback', 'owners': {tid}, 'ownerTrackId': tid}

    for requirement in contexts():
        inferred = requirement['evidence'] == 'expressive_response'
        fallback = requirement['evidence'] == 'unsupported_lead_fallback'
        owners = {tid for tid in set(requirement['owners']) & available
                  if inferred or fallback or peer_eligible is None or peer_eligible(tid, requirement['start'], requirement['end'])}
        occupied = [(row['start'], row['end']) for tid in owners for key, row in rows[tid].items()
                    if (tid, *key) in selected and row['start'] < requirement['end']-EPS
                    and row['end'] > requirement['start']+EPS]
        # A wholly absent solo is checked by the existing owner oracle. This
        # pass establishes only local rests in an actually selected lead voice.
        if not occupied and not fallback:
            continue
        if inferred or fallback:
            # Unknown-source inference never demands replacement of an already
            # selected physical gesture. This is distinct from the stronger
            # named-owner check, where backing cannot hide a missing soloist.
            first = bisect_right(physical_ends, requirement['start']+EPS)
            last = bisect_left(physical_starts, requirement['end']-EPS)
            occupied = physical[first:last]
        if fallback:
            # Any representable owner gesture is still authoritative, even if
            # another coverage check found it was improperly left unselected.
            # Raw-linked projected fragments are independently excluded from
            # supported_rows; selected physical fragments still block above.
            occupied = [*occupied, *((row['start'], row['end'])
                        for tid in owners for row in supported_rows[tid].values())]
        for start, end in rests(requirement['start'], requirement['end'], occupied):
            if fallback:
                start = max(start + .25, quarter_at(recording_at(start) + .125)) if quarter_at else start + .25
                end = min(end - .25, quarter_at(recording_at(end) - .125)) if quarter_at else end - .25
            if end - start < 1 - EPS:
                continue
            candidates = {}
            peers = (set(fallback_sources) & available) - owners if fallback else (owners & requirement['responsePeers'] if inferred else owners)
            for tid in sorted(peers):
                if inferred or fallback:
                    members = {key: row for batch in _complete_responses(indices[tid], start, end, budget,
                                                                        supported_rows[tid].keys() if fallback else None)
                               if _expressive_response(batch, local=not fallback, tuning=(source_tunings or {}).get(tid, ()))
                               for key, row in batch.items()}
                    if members:
                        candidates[tid] = members
                else:
                    members = {key: row for _, _, component in contained(indices[tid], start, end, budget)
                               for key, row in component.items()}
                    if _meaningful(members):
                        candidates[tid] = members
                if budget['limited']:
                    break
            if not candidates:
                if budget['limited']:
                    break
                continue
            key = round(start, 5), round(end, 5), tuple(candidates)
            if key in seen:
                continue
            seen.add(key)
            unresolved.append({'sectionName': requirement.get('sectionName', ''),
                               'start': round(start, 6), 'end': round(end, 6),
                               'recordingStart': round(recording_at(start), 6),
                               'recordingEnd': round(recording_at(end), 6),
                               'candidateTrackIds': sorted(candidates),
                               'candidateEventCounts': {tid: len(members) for tid, members in candidates.items()},
                               **({'ownerTrackId': requirement['ownerTrackId']} if fallback else {}),
                               'reason': ('unfilled_unsupported_lead_fallback' if fallback else
                                          'unfilled_expressive_peer_response' if inferred else 'unfilled_named_lead_handover')})
            if budget['limited']:
                break
        if budget['limited']:
            break
    return {'scope': 'named_solo_rests_strong_responses_and_proven_unsupported_lead_fallbacks',
            'unresolvedCount': len(unresolved), 'unresolved': unresolved[:MAX_REPORTED_OPPORTUNITIES],
            'truncated': len(unresolved) > MAX_REPORTED_OPPORTUNITIES,
            'budgetLimited': budget['limited'], 'groupChecks': budget['checks'], 'maxGroupChecks': max_group_checks}


def active_quarters(values, part, start, end, quarter_at):
    """Written occupied slots and sounding durations, never gesture bridges."""
    intervals = [(quarter_at(float(b['time'])), quarter_at(float(b['end'])))
                 for b in part.get('notation_beats', []) if not b['rest']]
    intervals.extend((quarter_at(n['t']), quarter_at(n['t'] + n.get('sus', 0)))
                     for row in values.values() for n in row['notes'])
    return sum(b - a for a, b in joined((max(start, a), min(end, b)) for a, b in intervals))


def _activity_index(values, part, quarter_at):
    intervals = [(quarter_at(float(b['time'])), quarter_at(float(b['end'])))
                 for b in part.get('notation_beats', []) if not b['rest']]
    intervals.extend((quarter_at(n['t']), quarter_at(n['t'] + n.get('sus', 0)))
                     for row in values.values() for n in row['notes'])
    spans = joined(intervals)
    starts, prefix, total = [], [], 0.0
    for start, end in spans:
        starts.append(start)
        prefix.append(total)
        total += end - start
    def integral(point):
        i = bisect_right(starts, point) - 1
        return 0.0 if i < 0 else prefix[i] + min(point - spans[i][0], spans[i][1] - spans[i][0])
    return lambda start, end: integral(end) - integral(start)


def optional_fill_audit(rows, parts, selected, compatible, excluded, duration_quarters, quarter_at, recording_at, *, max_group_checks=MAX_GROUP_CHECKS):
    """Report safe source opportunities; optional material is never mandatory.

    This conservative warning inventory is not an optimizer or a promise that
    each opportunity is the musically best fill. Transition fret movement and
    listening quality remain outside its scope.
    """
    occupied = [(row['start'], row['end']) for tid, values in rows.items() for key, row in values.items()
                if (tid, *key) in selected]
    eligible = {tid for tid in compatible - excluded
                if not re.search(r'\b(?:delay|echo|effects?|fx)\b', parts[tid]['source'].name, re.I)}
    indices = {tid: ([a for a, _, _ in entries], entries)
               for tid in eligible for entries in [groups(rows[tid])]}
    activity = {tid: _activity_index(rows[tid], parts[tid], quarter_at) for tid in eligible}
    budget = {'checks': 0, 'maximum': max_group_checks, 'limited': False}
    opportunities = []
    for start, end in rests(0, duration_quarters, occupied):
        left, right = start, end
        if start > EPS:
            left = max(start + .25, quarter_at(recording_at(start) + .125))
        if end < duration_quarters - EPS:
            right = min(end - .25, quarter_at(recording_at(end) - .125))
        if right - left < 4 - EPS:
            continue
        candidates = {}
        for tid in sorted(eligible):
            members = {key: row for _, _, component in contained(indices[tid], left, right, budget)
                       for key, row in component.items()}
            attacks = {round(row['onset'], 5) for row in members.values() if any(pitched(n) for n in row['notes'])}
            if len(attacks) < 2:
                if budget['limited']:
                    break
                continue
            first, last = min(r['start'] for r in members.values()), max(r['end'] for r in members.values())
            active = activity[tid](first, last)
            if active >= 4 - EPS:
                candidates[tid] = round(active, 6)
            if budget['limited']:
                break
        if candidates:
            opportunities.append({'start': round(left, 6), 'end': round(right, 6),
                                  'recordingStart': round(recording_at(left), 6),
                                  'recordingEnd': round(recording_at(right), 6),
                                  'candidateTrackIds': sorted(candidates), 'activeQuarterBeats': candidates})
        if budget['limited']:
            break
    return {'scope': 'safe_compatible_source_gestures_in_guarded_rests',
            'opportunityCount': len(opportunities), 'opportunities': opportunities[:MAX_REPORTED_OPPORTUNITIES],
            'truncated': len(opportunities) > MAX_REPORTED_OPPORTUNITIES,
            'budgetLimited': budget['limited'], 'groupChecks': budget['checks'], 'maxGroupChecks': max_group_checks,
            'unverifiedClaim': 'These source opportunities are not mandatory fills or proof of musical preference.'}
