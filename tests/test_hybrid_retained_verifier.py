"""The short-fragment exception only retains real neighboring accompaniment."""
from copy import deepcopy

import pytest

from feedback_converter.song_import.verification import Check
from feedback_converter.song_import.verify_hybrid_optional import audit_retained_boundary


def fixture():
    refs = [{'kind': 'notes', 'index': i} for i in range(24)]
    parent = {'trackId': 'backing', 'start': 0, 'end': 12, 'events': refs,
              'boundaryQuarters': [0, 12], 'boundaries': ['song', 'section']}
    episode = {'trackId': 'melody', 'start': 4, 'end': 8,
               'events': [{'kind': 'notes', 'index': i} for i in range(8)]}
    passage = {'trackId': 'backing', 'start': 8.5, 'end': 12, 'events': deepcopy(refs[17:]),
               'variant': {'kind': 'retained_optional_boundary', 'parentStart': 0, 'parentEnd': 12,
                           'parentBoundaryQuarters': [0, 12], 'parentBoundaries': ['song', 'section'],
                           'retainedParent': parent, 'foregroundEpisode': deepcopy(episode), 'retentionSide': 'after'}}
    receipt = {'passages': [episode, passage]}
    rows = {'backing': {('notes', i): {'onset': i/2, 'start': i/2, 'end': i/2+.5} for i in range(24)}}
    return passage, receipt, rows


def audit(values):
    check = Check()
    audit_retained_boundary(*values, lambda q: q/2, check)
    return check.errors


def test_short_intact_suffix_can_survive_foreground_replacement():
    assert not audit(fixture())


@pytest.mark.parametrize('fault', ['missing_episode', 'primary_episode', 'wrong_source', 'outer_edge',
                                   'fabricated_parent', 'cropped_group', 'guard', 'chain', 'duplicate_side'])
def test_retention_exception_cannot_authorize_arbitrary_short_fill(fault):
    values = fixture()
    passage, receipt, rows = values
    if fault == 'missing_episode':
        receipt['passages'].pop(0)
    elif fault == 'primary_episode':
        receipt['passages'][0]['priority'] = 'solo'
    elif fault == 'wrong_source':
        passage['variant']['retainedParent']['trackId'] = 'other'
    elif fault == 'outer_edge':
        passage['end'] = 11.5
        passage['events'].pop()
    elif fault == 'fabricated_parent':
        passage['variant']['retainedParent']['events'].pop(1)
    elif fault == 'cropped_group':
        rows['backing'][('notes', 23)]['end'] = 12.5
    elif fault == 'guard':
        passage['start'] = 8.1
    elif fault == 'chain':
        passage['variant']['retainedParent']['variant'] = {'kind': 'retained_optional_boundary'}
    elif fault == 'duplicate_side':
        receipt['passages'].append(deepcopy(passage))
    assert 'hybrid_retained_boundary' in {e['code'] for e in audit(values)}
