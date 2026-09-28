"""The tiny accompaniment-edge allowance is measured from independent facts."""
from copy import deepcopy
from fractions import Fraction as F

import pytest

from feedback_converter.song_import.verification import Check
from feedback_converter.song_import.verify_hybrid_optional import audit_edge_omissions
from feedback_converter.song_import.verify_source import Atom, Bar, Part, Source
from feedback_converter.song_import.verify_timeline import Clock


def fixture():
    bars = [Bar((4, 4), tempos={F(0): F(120)} if i == 0 else {}) for i in range(4)]
    part = Part('g', 'Guitar', 'guitar', [40, 45, 50, 55, 59, 64], 0,
                [[Atom(F(j, 2), F(1, 2), 0, 5 + j, f'{i}/{j}', '0', f'{i}/{j}') for j in range(8)] for i in range(4)],
                [[{'q': F(j, 2), 'length': F(1, 2), 'rest': False} for j in range(8)] for _ in range(4)])
    main = deepcopy(part)
    main.id = 'main'
    source = Source('songsterr', 'Test', 'Test', bars, [main, part], [], 1, {})
    refs = [{'kind': 'notes', 'index': i, 'sourceIds': [f'n{i}'], 'occurrences': [i // 8 + 1]} for i in range(32)]
    rows = {'g': {('notes', i): {'onset': i / 2, 'start': i / 2, 'end': (i + 1) / 2,
                                'notes': [{'f': 5 + i % 8, 't': i / 4}],
                                'sourceIds': ref['sourceIds'], 'occurrences': ref['occurrences']}
                  for i, ref in enumerate(refs)}}
    parent = {'trackId': 'g', 'start': 4, 'end': 12, 'boundaryQuarters': [4, 12],
              'boundaries': ['repeat', 'repeat'], 'events': deepcopy(refs[8:24])}
    episode = {'trackId': 'melody', 'start': 4.5, 'end': 11.25, 'events': [{'kind': 'notes', 'index': 0}]}
    record = {'trackId': 'g', 'side': 'after', 'start': 11.5, 'end': 12, 'quarterBeats': .5,
              'pitchedAttackCount': 1, 'events': deepcopy(refs[23:24]), 'incumbentParent': parent,
              'foregroundEpisode': deepcopy(episode)}
    receipt = {'passages': [episode], 'selection': {'optionalForeground': {'allowedEdgeOmissions': [record]}}}
    return receipt, rows, {'g': {'source': part}, 'main': {'source': main}}, source, [0, 1, 2, 3]


def audit(values, protected=()):
    receipt, rows, parts, source, order = values
    check = Check()
    audit_edge_omissions(receipt, rows, parts, source, order, Clock(source, order), lambda q: q / 2,
                        lambda seconds: seconds * 2, protected, 'main', check)
    return check.errors


def test_whole_half_beat_edge_beside_selected_optional_foreground_is_bounded():
    assert not audit(fixture(), [(0, 3.75), (12.25, 16)])


@pytest.mark.parametrize('fault', ['not_selected', 'primary_episode', 'main_source', 'wrong_setup', 'incomplete_parent',
                                   'cropped_dependency', 'ghost', 'second_attack', 'wrong_lineage', 'wrong_occurrence',
                                   'duplicate_edge', 'already_selected', 'false_duration', 'unsupported_parent_boundary',
                                   'too_far', 'too_large_fraction', 'wrong_outer_edge', 'guard', 'protected_primary'])
def test_edge_allowance_rejects_unproved_or_overbroad_losses(fault):
    values = fixture()
    receipt, rows, parts, _, _ = values
    records = receipt['selection']['optionalForeground']['allowedEdgeOmissions']
    record = records[0]
    protected = ()
    if fault == 'not_selected':
        receipt['passages'].clear()
    elif fault == 'primary_episode':
        receipt['passages'][0]['priority'] = 'solo'
    elif fault == 'main_source':
        record['trackId'] = record['incumbentParent']['trackId'] = 'main'
    elif fault == 'wrong_setup':
        parts['g']['source'].capo = 2
    elif fault == 'incomplete_parent':
        record['incumbentParent']['events'].pop(0)
    elif fault == 'cropped_dependency':
        rows['g'][('notes', 23)]['start'] = 11
    elif fault == 'ghost':
        rows['g'][('notes', 23)]['notes'][0]['ghost'] = True
    elif fault == 'second_attack':
        record['start'] = 11
        record['quarterBeats'] = 1
        record['events'] = deepcopy(record['incumbentParent']['events'][-2:])
        record['pitchedAttackCount'] = 2
    elif fault == 'wrong_lineage':
        record['events'][0]['sourceIds'] = ['other']
    elif fault == 'wrong_occurrence':
        record['events'][0]['occurrences'] = [4]
    elif fault == 'duplicate_edge':
        records.append(deepcopy(record))
    elif fault == 'already_selected':
        receipt['passages'].append({'trackId': 'g', 'start': 11.5, 'end': 12, 'events': deepcopy(record['events'])})
    elif fault == 'false_duration':
        record['quarterBeats'] = .25
    elif fault == 'unsupported_parent_boundary':
        record['incumbentParent']['boundaries'][0] = 'section'
    elif fault in {'too_far', 'too_large_fraction', 'guard'}:
        for episode in (record['foregroundEpisode'], receipt['passages'][0]):
            if fault == 'too_far':
                episode['end'] = 10
            elif fault == 'guard':
                episode['end'] = 11.4
            else:
                episode['start'] = 10.5
    elif fault == 'wrong_outer_edge':
        record['start'], record['end'] = 11, 11.5
        record['events'] = deepcopy(record['incumbentParent']['events'][-2:-1])
    elif fault == 'protected_primary':
        protected = [(11.5, 12)]
    assert audit(values, protected)


def test_both_edges_share_the_same_twenty_percent_budget():
    values = fixture()
    receipt = values[0]
    records = receipt['selection']['optionalForeground']['allowedEdgeOmissions']
    after = records[0]
    parent = after['incumbentParent']
    parent.update(end=9.5, boundaryQuarters=[4, 9.5], boundaries=['repeat', 'gesture'],
                  variant={'kind': 'gap_safe_subphrase', 'parentStart': 4, 'parentEnd': 12,
                           'parentBoundaryQuarters': [4, 12], 'parentBoundaries': ['repeat', 'repeat']})
    parent['events'] = parent['events'][:11]
    for episode in (after['foregroundEpisode'], receipt['passages'][0]):
        episode.update(start=4.75, end=8.75)
    after.update(start=9, end=9.5, events=deepcopy(parent['events'][-1:]))
    before = deepcopy(after)
    before.update(side='before', start=4, end=4.5, events=deepcopy(parent['events'][:1]))
    assert not audit(values)  # Either half-beat edge is individually within 20%.
    records.append(before)
    errors = audit(values)
    assert len(errors) == 1
    assert 'combined omitted edges' in errors[0]['message']
