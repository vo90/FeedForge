"""Explicit projection of opening bars skipped by a Songsterr recording clock."""
from math import isfinite

from .audio import ImportFailure

POLICY = 'songsterr-collapsed-opening-v1'


def prefix(points):
    """Exact equality only, and only before the first positive interval."""
    if len(points) < 2:
        raise ValueError('Missing recording boundaries')
    count = 0
    while count + 1 < len(points) and points[count + 1] == points[0]:
        count += 1
    if count == len(points) - 1 or any(b <= a for a, b in zip(points[count:], points[count + 1:])):
        raise ValueError('Non-increasing recording boundaries')
    return count


def score_end(alignment):
    policy = alignment.get('collapsedOpening')
    return policy['scoreEnd'] if policy else 0.0


def omitted(alignment, start):
    return bool(alignment.get('collapsedOpening')) and start < score_end(alignment) - 1e-8


def plan(performance, anchors, count):
    """No repair of notes crossing the jump, linked attacks or strum groups."""
    boundary = anchors[count]['score']
    def fail():
        raise ImportFailure('source_sync_unavailable', 'The skipped opening crosses a playable gesture; automatic matching is required.',
                            {'sourceSyncReason': 'collapsed_opening_crossing'})
    note_count = 0
    for track in performance['tracks']:
        notes = [(n, n['t']) for n in track.get('notes', [])]
        notes += [(n, n.get('t', c['t'])) for c in track.get('chords', []) for n in c['notes']]
        remaining = 0
        previous = {}
        for note, start in sorted(notes, key=lambda pair: (pair[1], pair[0]['s'])):
            end = start + note.get('sus', 0)
            if not all(isfinite(x) for x in (start, end)) or end < start:
                fail()
            if start < boundary - 1e-8:
                if start < 0 or end > boundary + 1e-8:
                    fail()
                note_count += 1
            else:
                remaining += 1
                prior = previous.get(note['s'])
                if prior and prior[1] < boundary - 1e-8 and (prior[0].get('ln') or note.get('ho') or note.get('po')):
                    fail()
            previous[note['s']] = note, start
        if notes and not remaining:
            fail()
        for chord in track.get('chords', []):
            sides = {n.get('t', chord['t']) < boundary - 1e-8 for n in chord['notes']}
            if len(sides) > 1:
                fail()
    for group in performance.get('strumEvidence', []):
        if len({n['t'] < boundary - 1e-8 for n in group['notes']}) > 1:
            fail()
    return {'version': 1, 'policy': POLICY, 'measureCount': count,
            'scoreEnd': boundary, 'recordingTime': anchors[count]['audio'], 'noteCount': note_count}


def receipt(performance, alignment, source_hash):
    rows = []
    for track in performance['tracks']:
        notes = [(n, n['t']) for n in track.get('notes', [])]
        notes += [(n, n.get('t', c['t'])) for c in track.get('chords', []) for n in c['notes']]
        for n, start in notes:
            if omitted(alignment, start):
                rows.append({'trackId': track['id'], 'sourceIds': sorted(set(n['source_ids'])),
                             'string': n['s'], 'fret': n['f'], 'scoreStart': start,
                             'scoreEnd': start + n.get('sus', 0)})
    rows.sort(key=lambda n: (n['trackId'], n['scoreStart'], n['string'], n['sourceIds']))
    return {**alignment['collapsedOpening'], 'sourceSha256': source_hash,
            'timeDomain': 'score_seconds', 'notes': rows}


def notice(policy):
    return (f"Songsterr's recording timing skips {policy['measureCount']} opening bar(s). "
            f"{policy['noteCount']} opening note(s) were omitted from gameplay and scoring. "
            'The original tab and omission details are retained in the FeedPak.')
