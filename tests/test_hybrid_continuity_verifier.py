"""Forged join claims cannot bypass raw musical and traversal boundaries."""
from copy import deepcopy
from fractions import Fraction as F

import pytest

from feedback_converter.song_import.verification import Check
from feedback_converter.song_import.verify_source import Atom, Bar, Part, Source
from feedback_converter.song_import.verify_timeline import Clock
from feedback_converter.song_import.verify_hybrid_continuity import audit_section_join


def fixture():
    bars = [Bar((4, 4), section='Verse' if i == 2 else '', tempos={F(0): F(120)} if i == 0 else {})
            for i in range(4)]
    part = Part('g', 'Guitar', 'guitar', [40, 45, 50, 55, 59, 64], 0,
                [[Atom(F(j), F(1), 0, 5 + j, f'{i}/{j}', '0', f'{i}/{j}') for j in range(4)] for i in range(4)],
                [[{'q': F(j), 'length': F(1), 'rest': False} for j in range(4)] for _ in range(4)])
    source = Source('songsterr', 'Test', 'Test', bars, [part], [], 1, {})
    order = [0, 1, 2, 3]
    parents = [{'start': 4, 'end': 8, 'boundaryQuarters': [4, 8], 'boundaries': ['repeat', 'section']},
               {'start': 8, 'end': 12, 'boundaryQuarters': [8, 12], 'boundaries': ['section', 'repeat']}]
    passage = {'trackId': 'g', 'start': 5, 'end': 11, 'variant': {
        'kind': 'gap_safe_subphrase', 'parentStart': 4, 'parentEnd': 12,
        'parentBoundaryQuarters': [4, 12], 'parentPhraseCount': 2,
        'sectionJoin': {'quarter': 8, 'barsPerSide': 1, 'proofStart': 4, 'proofEnd': 12,
                        'meter': {'numerator': 4, 'denominator': 4}, 'bpm': 120, 'parents': parents}}}
    return passage, source, part, order


def audit(values, events=None):
    passage, source, part, order = values
    check = Check()
    audit_section_join(passage, source, part, order, Clock(source, order), events or {}, check)
    return check.errors


def test_complete_raw_repeat_can_continue_across_marker():
    assert not audit(fixture())


@pytest.mark.parametrize('fault', ['raw_technique', 'tempo', 'meter', 'jump', 'missing_proof',
                                   'extra_marker', 'parent_gap', 'proof_size', 'not_crossed', 'hard_group'])
def test_continuity_mutations_are_rejected(fault):
    values = fixture()
    passage, source, part, order = values
    events = {}
    if fault == 'raw_technique':
        part.bars[2][1].effects['vibrato'] = True
    elif fault == 'tempo':
        source.bars[2].tempos[F(1)] = F(121)
    elif fault == 'meter':
        source.bars[2].signature = (8, 8)
    elif fault == 'jump':
        order[2:] = [0, 1]
        source.bars[0].section = 'Verse'
    elif fault == 'missing_proof':
        passage['variant'].pop('sectionJoin')
    elif fault == 'extra_marker':
        source.bars[1].section = 'Other'
        passage['variant']['sectionJoin'].update(barsPerSide=2, proofStart=0, proofEnd=16)
    elif fault == 'parent_gap':
        passage['variant']['sectionJoin']['parents'][1]['start'] = 8.5
    elif fault == 'proof_size':
        passage['variant']['sectionJoin']['barsPerSide'] = True
    elif fault == 'not_crossed':
        passage['end'] = 8
    elif fault == 'hard_group':
        events[('notes', 0)] = {'start': 7, 'end': 9}
    assert 'hybrid_section_join' in {e['code'] for e in audit(values, events)}


@pytest.mark.parametrize('blocker', ['written_slot', 'linked_gesture'])
def test_ordinary_subphrase_does_not_require_a_section_join_at_a_blocked_marker(blocker):
    values = fixture()
    values[0]['variant'].pop('sectionJoin')
    values[0]['variant'].pop('parentPhraseCount')
    events = {}
    if blocker == 'written_slot':
        values[2].beats[1][-1]['length'] = F(2)  # Written q7-9 crosses the q8 marker.
    else:
        events[('notes', 0)] = {'start': 7, 'end': 9}
    assert not audit(values, events)


@pytest.mark.parametrize('declared_count', [None, 1])
@pytest.mark.parametrize('raw_changed', [False, True])
def test_stripping_join_metadata_cannot_hide_a_freely_cuttable_section(declared_count, raw_changed):
    values = fixture()
    variant = values[0]['variant']
    variant.pop('sectionJoin')
    if declared_count is None:
        variant.pop('parentPhraseCount')
    else:
        variant['parentPhraseCount'] = declared_count
    if raw_changed:
        values[2].bars[2][1].effects['vibrato'] = True
    assert 'hybrid_section_join' in {e['code'] for e in audit(values)}
