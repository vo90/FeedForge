"""Construct the primary lead before optional accompaniment is considered.

The receipt accounts for every performed chart event. This is production
selection; verify_hybrid_priority independently audits its coverage and priority.
"""
from copy import deepcopy
import re

from .audio import ImportFailure

EPS = 1e-7


def suggested_role(track):
    from .hybrid_selection import suggested_role as infer_role
    return infer_role(track)


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
                role = 'accompaniment'
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
    from .hybrid_regional import backbone as regional_backbone
    return regional_backbone(performance, options, main_id, alignment, audio_duration, originals)


def finish(result, primary, performance, options, alignment):
    from .hybrid_regional import finish as regional_finish
    return regional_finish(result, primary, performance, options, alignment)
