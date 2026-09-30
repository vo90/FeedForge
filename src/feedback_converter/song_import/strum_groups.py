"""Bind explicit brush gestures to the existing display-only note `ch` field.

Notes stay in their original event arrays and keep all attack times/effects.
IDs are package-global so a Hybrid can copy several sources without collisions.
Incomplete/ambiguous gestures, arpeggios and already simultaneous chords are
left to their ordinary presentation. Never group merely neighbouring notes.
"""
from collections import defaultdict
from .alignment import map_time

POLICY = 'authored-brush-groups-v1'


def attach(chart, track_id, groups, alignment):
    members = defaultdict(list)
    for note in chart['notes']:
        members[(round(note['t'], 6), note['s'], note['f'])].append(note)
    for ident, group in enumerate(groups):
        if group['trackId'] != track_id or group.get('kind') != 'brush':
            continue
        keys = [(round(map_time(alignment, n['t']), 6), n['s'], n['f']) for n in group['notes']]
        if (len(keys) < 2 or len({k[0] for k in keys}) < 2
                or len({k[1] for k in keys}) != len(keys)
                or any(len(members[k]) != 1 for k in keys)):
            continue
        for key in keys:
            members[key][0]['ch'] = ident
