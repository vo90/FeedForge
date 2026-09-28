"""Conservative content evidence for foreground among already-safe optional parts.

This proposes exact complete-candidate episodes, never changes source membership
or track-wide priority. The caller retains surrounding accompaniment and applies
ordinary transition/search rules. Uncertain parallel voices keep that search's
existing result. Labels, register and note density confer no musical advantage.
"""
from bisect import bisect_left, bisect_right
from collections import Counter
from hashlib import sha256
import json
from statistics import median

from .hybrid_selection import _local, _clear_local_advantage, _musical_score

EPS = 1e-7
MAX_CANDIDATES = 4096
MAX_OPERATIONS = 200000
MAX_EPISODES = 128


class _Limit(Exception):
    pass


def _expression(note):
    families = set()
    if note.get('bn') or note.get('bnv'):
        families.add('bend')
    if note.get('vb'):
        families.add('vibrato')
    if note.get('hp') or note.get('ho') or note.get('po'):
        families.add('legato')
    # Zero is a valid destination fret, not an absent slide.
    if note.get('sl') is not None or note.get('slu') is not None:
        families.add('slide')
    return families


def _features(track, rows, lo, hi, clock):
    data = _local(track, rows, lo, hi, clock)
    if not data:
        return None
    groups, families, widths, voicings, lengths = [], set(), Counter(), {}, {}
    for row in sorted(rows, key=lambda r: (r['start'], r['end'], r['kind'], r['index'])):
        pitched = [n for n in row['notes'] if not n.get('ghost') and not n.get('mt')
                   and lo - EPS <= clock.quarter(n['t']) < hi - EPS]
        if not pitched:
            continue
        expression = set().union(*(_expression(n) for n in pitched))
        families.update(expression)
        for note in pitched:
            onset = round(clock.quarter(note['t']), 8)
            widths[onset] += 1
            voicings.setdefault(onset, []).append((note['s'], note['f']))
            lengths[onset] = max(lengths.get(onset, 0),
                                 clock.quarter(note['t'] + note.get('sus', 0)) - onset)
        # A many-point connected glide is one gesture, not dozens of
        # independent expressive phrases. Strictly touching groups separate.
        if groups and row['start'] < groups[-1][1] - EPS:
            groups[-1][1] = max(groups[-1][1], row['end'])
            groups[-1][2].update(expression)
        else:
            groups.append([row['start'], row['end'], expression])
    return {**data, 'gestureCount': len(groups),
            'expressiveGestures': sum(bool(g[2]) for g in groups),
            'expressionFamilies': sorted(families),
            'broadChordFraction': sum(n >= 3 for n in widths.values()) / max(1, len(widths)),
            'staticVoicingFraction': max(Counter(tuple(sorted(v)) for v in voicings.values()).values(), default=0)
                                     / max(1, len(voicings)),
            'medianAttackDuration': median(lengths.values()) if lengths else 0}


def _melody(data):
    # A repeated expressive melody remains eligible; repetition alone cannot
    # demote it. Articulations must recur in independent complete gestures.
    return bool(data and data['attacks'] >= 6 and data['variety'] >= 4
                and data['coverage'] >= .65
                and data['single'] >= .75 and data['expressive'] >= .2
                and data['gestureCount'] >= 3 and data['expressiveGestures'] >= 2
                and max(data['palmMute'], data['letRing']) < .35
                and (len(data['expressionFamilies']) >= 2
                     or (data['expressiveGestures'] >= 3 and data['rhythmVariety'] >= .3)))


def _backing(data, context):
    if not data or not context:
        return False
    # A double-stop solo and a recurring palm-muted single-note lead riff are
    # not proven accompaniment. Demand repeating arpeggio/strum texture, with
    # low expression both locally and in the bounded surrounding context.
    texture = (context['letRing'] >= .5
               or (context['broadChordFraction'] >= .5 and data['broadChordFraction'] >= .5))
    repeated_texture = context['motifRepeat'] >= .5 and texture
    # A stationary, unexpressive held dyad/drone can have too few attacks for
    # repeated four-attack cells. This cannot classify a moving double-stop
    # solo: repeated voicing, no articulations and long durations must agree.
    held_drone = (context['single'] == 0 and context['staticVoicingFraction'] >= .8
                  and context['medianAttackDuration'] >= 1 and context['expressive'] == 0
                  and data['expressive'] == 0 and data['single'] == 0)
    return (context['attacks'] >= 4 and (repeated_texture or held_drone)
            and context['expressive'] < .12 and data['expressive'] < .12
            )


def _summary(data):
    return {k: round(v, 6) if isinstance(v, float) else v for k, v in data.items()
            if k not in {'pitch', 'first', 'last'}}


def recognize(candidates, rows_by_track, tracks_by_id, clock, *, options=None,
              main_id=None, max_candidates=MAX_CANDIDATES,
              max_operations=MAX_OPERATIONS, max_episodes=MAX_EPISODES):
    """Return bounded, nonoverlapping proposals and reproducible content facts.

    Call with one guarded optional gap's admitted candidates. ``rows_by_track``
    contains complete performed source groups, not clipped screen windows.
    Candidate indices refer to the unchanged input list. The function rechecks
    complete membership and setup; import/omission admission remains the caller's
    responsibility. Explicit exclusions, roles and preferred ordering win.

    Work units count indexed rows/atoms, feature rows/atoms, and comparisons.
    Exhaustion proposes nothing, leaving the ordinary feasible search intact.
    """
    options = options or {}
    operations = 0
    result = {'algorithm': 'local-optional-foreground-v1', 'episodes': [],
              'candidateCount': len(candidates), 'operations': 0,
              'budgetLimited': False, 'reason': None}

    def spend(count=1):
        nonlocal operations
        if count > max_operations - operations:
            raise _Limit('operation_limit')
        operations += count

    def limited(reason):
        return {**result, 'operations': operations, 'budgetLimited': True, 'reason': reason}

    if len(candidates) > max_candidates:
        return limited('candidate_limit')
    if len({p['trackId'] for p in candidates}) < 2:
        return result
    preferred = list(dict.fromkeys(options.get('preferredTrackIds', [])))
    exclusions = set(options.get('excludedTrackIds', []))
    roles = options.get('roles', {})
    main = tracks_by_id.get(main_id)

    try:
        indexed = {}
        for tid in sorted({p['trackId'] for p in candidates}):
            source = rows_by_track.get(tid, [])
            for row in source:
                spend(1 + len(row['notes']))
            ordered = sorted(source, key=lambda r: (r['start'], r['end'], r['kind'], r['index']))
            ends, high = [], float('-inf')
            for row in ordered:
                high = max(high, row['end'])
                ends.append(high)
            indexed[tid] = (ordered, [r['start'] for r in ordered], ends,
                            {(r['kind'], r['index']): r for r in ordered})

        def between(tid, lo, hi):
            ordered, starts, ends, _ = indexed[tid]
            first = bisect_right(ends, lo + EPS)
            last = bisect_left(starts, hi - EPS)
            found = ordered[first:last]
            spend(len(found) + sum(len(r['notes']) for r in found))
            return [r for r in found if r['end'] > lo + EPS and r['start'] < hi - EPS]

        details = {}
        for index, passage in enumerate(candidates):
            spend()
            tid = passage['trackId']
            track = tracks_by_id.get(tid)
            if (not track or track.get('instrument') != 'guitar' or tid in exclusions
                    or (main and (track['tuning'] != main['tuning']
                                  or track.get('capo', 0) != main.get('capo', 0)))):
                continue
            keys = {(r['kind'], r['index']) for r in passage.get('events', [])}
            spend(len(keys))
            lookup = indexed[tid][3]
            if not keys or not keys <= lookup.keys():
                continue
            selected = [lookup[key] for key in sorted(keys)]
            lo, hi = passage['start'], passage['end']
            if (any(r['start'] < lo - EPS or r['end'] > hi + EPS or not r.get('available', True)
                    for r in selected)
                    or {(r['kind'], r['index']) for r in between(tid, lo, hi)} != keys):
                continue
            spend(len(selected) + sum(len(r['notes']) for r in selected))
            data = _features(track, selected, lo, hi, clock)
            if not data:
                continue
            identity = sha256(json.dumps([tid, lo, hi, sorted(keys)], separators=(',', ':')).encode()).hexdigest()[:20]
            details[index] = {'rows': selected, 'features': data, 'id': 'optional-' + identity}

        proposed = []
        for index, value in details.items():
            passage = candidates[index]
            tid, lo, hi = passage['trackId'], passage['start'], passage['end']
            lead = value['features']
            if roles.get(tid) == 'accompaniment' or not _melody(lead):
                continue
            context_rows = between(tid, lo - 8, hi + 8)
            context = _features(tracks_by_id[tid], context_rows, lo - 8, hi + 8, clock)
            if _backing(lead, context):
                continue
            backing, comparisons, ambiguous = [], [], False
            for other_index, other_value in details.items():
                spend()
                other = candidates[other_index]
                oid = other['trackId']
                if oid == tid:
                    continue
                overlap = max(0, min(hi, other['end']) - max(lo, other['start']))
                if overlap <= EPS:
                    continue
                # A preferred or manually foreground source is not demoted by
                # an automatic classifier, even if its current notes repeat.
                if ((oid in preferred and (tid not in preferred or preferred.index(oid) < preferred.index(tid)))
                        or roles.get(oid) in {'lead', 'solo'}):
                    ambiguous = True
                    break
                spend(len(other_value['rows']) + sum(len(r['notes']) for r in other_value['rows']))
                local = _features(tracks_by_id[oid], other_value['rows'], lo, hi, clock)
                # A complete held event may overlap the episode without an
                # attack inside it. Its original candidate, never an invented
                # cropped note, still needs corroborated backing evidence.
                if local is None:
                    local = other_value['features']
                wider_rows = between(oid, lo - 8, hi + 8)
                wider = _features(tracks_by_id[oid], wider_rows, lo - 8, hi + 8, clock)
                clear = (local and (_clear_local_advantage(lead, local)
                         or (wider and _clear_local_advantage(lead, wider)
                             and lead['expressive'] >= local['expressive'] + .1
                             and _musical_score(lead) >= _musical_score(local) + .6)))
                if not _backing(local, wider) or not clear:
                    # Includes equally credible melodies and expressive dyads;
                    # the classifier intentionally does not choose their owner.
                    ambiguous = True
                    break
                backing.append(other_index)
                comparisons.append({'candidateIndex': other_index, 'candidateId': other_value['id'],
                                    'trackId': oid, 'local': _summary(local), 'context': _summary(wider),
                                    'musicalScore': round(_musical_score(local), 6)})
            if backing and not ambiguous:
                proposed.append({'candidateIndex': index, 'candidateId': value['id'], 'trackId': tid,
                                 'start': lo, 'end': hi, 'competingCandidateIndices': backing,
                                 'evidence': {'reason': 'corroborated_optional_foreground',
                                              'local': _summary(lead), 'context': _summary(context),
                                              'musicalScore': round(_musical_score(lead), 6),
                                              'competingAccompaniment': comparisons}})

        episodes = []
        for episode in sorted(proposed, key=lambda p: (-(p['end'] - p['start']),
                              -candidates[p['candidateIndex']].get('activeQuarterBeats', 0), p['candidateId'])):
            spend(len(episodes) + 1)
            if any(episode['start'] < p['end'] - EPS and episode['end'] > p['start'] + EPS for p in episodes):
                continue
            if len(episodes) >= max_episodes:
                raise _Limit('episode_limit')
            episodes.append(episode)
        return {**result, 'episodes': sorted(episodes, key=lambda p: (p['start'], p['end'], p['candidateId'])),
                'operations': operations}
    except _Limit as exc:
        return limited(str(exc))
