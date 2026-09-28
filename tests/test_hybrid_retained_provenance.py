"""A surviving edge must retain its original independently valid provenance."""
from copy import deepcopy
from fractions import Fraction as F

import pytest

from feedback_converter.song_import.verification import Check
from feedback_converter.song_import.verify_hybrid_optional import audit_retained_boundary, audit_retained_parent_source
from feedback_converter.song_import.verify_source import Atom, Bar, Part, Source
from feedback_converter.song_import.verify_timeline import Clock


def fixture():
    bars = [Bar((4, 4), section='Verse' if i == 2 else '', tempos={F(0): F(120)} if i == 0 else {})
            for i in range(4)]
    part = Part('g', 'Guitar', 'guitar', [40, 45, 50, 55, 59, 64], 0,
                [[Atom(F(j), F(1), 0, 5 + j, f'{i}/{j}', '0', f'{i}/{j}') for j in range(4)] for i in range(4)],
                [[{'q': F(j), 'length': F(1), 'rest': False} for j in range(4)] for _ in range(4)])
    source = Source('songsterr', 'Test', 'Test', bars, [part], [], 1, {})
    order = [0, 1, 2, 3]
    refs = [{'kind': 'notes', 'index': i} for i in range(16)]
    old = {'kind': 'gap_safe_subphrase', 'parentStart': 4, 'parentEnd': 12,
           'parentBoundaryQuarters': [4, 12], 'parentBoundaries': ['repeat', 'repeat'], 'parentPhraseCount': 2,
           'sectionJoin': {'quarter': 8, 'barsPerSide': 1, 'proofStart': 4, 'proofEnd': 12,
                           'meter': {'numerator': 4, 'denominator': 4}, 'bpm': 120,
                           'parents': [{'start': 4, 'end': 8, 'boundaryQuarters': [4, 8], 'boundaries': ['repeat', 'section']},
                                       {'start': 8, 'end': 12, 'boundaryQuarters': [8, 12], 'boundaries': ['section', 'repeat']}]}}
    parent = {'trackId': 'g', 'start': 5, 'end': 11, 'boundaryQuarters': [5, 11],
              'boundaries': ['gesture', 'gesture'], 'events': refs[5:11], 'variant': old}
    episode = {'trackId': 'melody', 'start': 6, 'end': 8, 'events': [{'kind': 'notes', 'index': 0}]}
    passage = {'trackId': 'g', 'start': 9, 'end': 11, 'events': deepcopy(refs[9:11]),
               'variant': {'kind': 'retained_optional_boundary', 'parentStart': 5, 'parentEnd': 11,
                           'parentBoundaryQuarters': [5, 11], 'parentBoundaries': ['gesture', 'gesture'],
                           'retainedParent': parent, 'foregroundEpisode': deepcopy(episode), 'retentionSide': 'after'}}
    rows = {'g': {('notes', i): {'onset': i, 'start': i, 'end': i + 1} for i in range(16)}}
    return passage, {'passages': [episode, passage]}, rows, source, part, order


def audit(values):
    passage, receipt, rows, source, part, order = values
    check = Check()
    audit_retained_boundary(passage, receipt, rows, lambda q: q / 2, check)
    audit_retained_parent_source(passage, source, part, order, Clock(source, order), rows['g'], check)
    return check.errors


def test_retained_suffix_may_be_entirely_after_its_original_join_marker():
    values = fixture()
    assert values[0]['start'] > values[0]['variant']['retainedParent']['variant']['sectionJoin']['quarter']
    assert not audit(values)


@pytest.mark.parametrize('fault', ['raw_technique', 'missing_proof', 'stripped_metadata', 'proof_bounds', 'tempo', 'nested_bounds',
                                   'natural_parent_boundary', 'missing_parent_variant'])
def test_retained_suffix_does_not_hide_invalid_original_provenance(fault):
    values = fixture()
    passage, _, _, source, part, _ = values
    parent = passage['variant']['retainedParent']
    old = parent['variant']
    if fault == 'raw_technique':
        part.bars[2][1].effects['vibrato'] = True
    elif fault == 'missing_proof':
        old.pop('sectionJoin')
    elif fault == 'stripped_metadata':
        old.pop('sectionJoin')
        old.pop('parentPhraseCount')
        part.bars[2][1].effects['vibrato'] = True
    elif fault == 'proof_bounds':
        old['sectionJoin']['proofEnd'] = 16
    elif fault == 'tempo':
        source.bars[2].tempos[F(1)] = F(121)
    elif fault == 'nested_bounds':
        old['parentEnd'] = 10
    elif fault == 'natural_parent_boundary':
        parent['boundaries'][0] = 'section'
        passage['variant']['parentBoundaries'][0] = 'section'
    elif fault == 'missing_parent_variant':
        parent.pop('variant')
    assert {e['code'] for e in audit(values)} & {'hybrid_retained_boundary', 'hybrid_section_join'}


def test_original_natural_parent_without_subphrase_proof_is_supported():
    values = fixture()
    values[3].bars[2].section = ''  # This original parent contains no separate source section.
    passage = values[0]
    parent = passage['variant']['retainedParent']
    parent.pop('variant')
    parent.update(start=4, end=12, boundaryQuarters=[4, 12], boundaries=['repeat', 'repeat'],
                  events=[{'kind': 'notes', 'index': i} for i in range(4, 12)])
    passage.update(end=12, events=[{'kind': 'notes', 'index': i} for i in range(9, 12)])
    passage['variant'].update(parentStart=4, parentEnd=12, parentBoundaryQuarters=[4, 12], parentBoundaries=['repeat', 'repeat'])
    assert not audit(values)


@pytest.mark.parametrize('raw_changed', [False, True])
def test_removing_old_variant_and_claiming_natural_outer_edges_cannot_hide_a_section(raw_changed):
    values = fixture()
    passage = values[0]
    parent = passage['variant']['retainedParent']
    parent.pop('variant')
    parent.update(start=4, end=12, boundaryQuarters=[4, 12], boundaries=['repeat', 'repeat'],
                  events=[{'kind': 'notes', 'index': i} for i in range(4, 12)])
    passage.update(end=12, events=[{'kind': 'notes', 'index': i} for i in range(9, 12)])
    passage['variant'].update(parentStart=4, parentEnd=12, parentBoundaryQuarters=[4, 12], parentBoundaries=['repeat', 'repeat'])
    if raw_changed:
        values[4].bars[2][1].effects['vibrato'] = True
    assert 'hybrid_section_join' in {error['code'] for error in audit(values)}
