"""Section/voice numbers are not evidence of a guitarist's identity."""
from copy import deepcopy
import json
from zipfile import ZipFile

import pytest

from feedback_converter.song_import.hybrid_selection import _named_people, regional_candidates
from feedback_converter.song_import.verify_hybrid_priority import regional_requirements
from test_hybrid_regional_validation import source_case, inspect, receipt_for
from test_hybrid_selection import track, performance
from test_song_import_score import beat, measure, raw_score
from test_songsterr_hybrid_lead import build, rest


DESCRIPTIONS = [
    'Part 2', 'PART 17', 'Pt. II', 'Part A', 'Section 3', 'Voice 2', 'Track IV',
    '2', '#12', 'II', '2nd', 'Second', 'Part Two', 'Lead Guitar', 'Clean',
    'Wah', 'with distortion', 'Talkbox', 'Intro', 'Outro', 'Verse 2',
    'Part No. 2', 'Part 2 of 3', 'Part Ⅱ',
]


@pytest.mark.parametrize('description', DESCRIPTIONS)
def test_descriptions_never_establish_named_ownership(tmp_path, description):
    label = f'Solo ({description})'
    a = track('a', f'Alex | Lead Guitar — {description}')
    b = track('b', 'Blake | Lead Guitar')
    assert not _named_people(label)
    candidates = regional_candidates(performance(a, b, sections=[{'name': label, 'time': 0}]), {}, 'b')
    assert not any(c['evidence'] == 'named_soloist' for c in candidates)

    source, facts, parts, _, rows = source_case(tmp_path)
    for bar in source.bars:
        bar.section = ''
    source.bars[1].section = label
    parts['1']['source'].name = a['name']
    assert not regional_requirements(parts, rows, source, facts['order'], 16)


@pytest.mark.parametrize('label', [
    'Solo (Adrian Smith)', 'Solo (Adrian Smith - Part 2)',
    'Solo (Adrian Smith, Part II)', 'Solo (Part A - Adrian Smith)',
    'Solo (Adrian Smith) (Voice 2)', 'Solo (Adrian Smith / Part Two)',
    'Solo (Lead Guitar: Adrian Smith)',
])
def test_qualifiers_do_not_erase_real_named_solo_or_allow_wrong_player(tmp_path, label):
    source, facts, parts, charts, rows = source_case(tmp_path)
    source.bars[1].section = label
    requirements = regional_requirements(parts, rows, source, facts['order'], 16)
    assert any(r['sectionName'] == label and set(r['owners']) == {'1'} for r in requirements)
    assert _named_people(label) == [{'adrian', 'smith'}]
    good = inspect(source, facts, charts, receipt_for(rows))
    assert not good.errors, good.errors
    wrong = inspect(source, facts, charts, receipt_for(rows, use_adrian=False))
    assert 'hybrid_primary_coverage' in {e['code'] for e in wrong.errors}


@pytest.mark.parametrize('names,label', [
    (('José González', 'Björn Gelotte'), 'Solo (José González & Björn Gelotte)'),
    (('Adrian Smith', 'Dave Murray'), 'Solo (Adrian Smith, Dave Murray) (Part II)'),
    (("D'Angelo", 'Anne-Marie'), "Solo (D'Angelo / Anne-Marie)"),
    (('D. Gilmour', 'Alex'), 'Solo (D. Gilmour / Alex) (Part II)'),
])
def test_real_names_still_offer_parallel_voices(tmp_path, names, label):
    source, facts, parts, _, rows = source_case(tmp_path)
    for bar in source.bars:
        bar.section = ''
    source.bars[1].section = label
    for tid, name in zip(('0', '1'), names):
        parts[tid]['source'].name = f'{name} | Lead Guitar'
    requirements = regional_requirements(parts, rows, source, facts['order'], 16)
    assert len(requirements) == 1 and set(requirements[0]['owners']) == {'0', '1'}
    assert len(_named_people(label)) == 2


def test_numbered_sections_with_two_voices_build_without_changing_original_charts(tmp_path):
    doc = raw_score([measure(beat(3)), measure(beat(5), marker={'text': 'Solo (Part 2)'}),
                     measure(beat(7)), measure(beat(3), marker={'text': 'Outro'})])
    doc['tracks'][0]['name'] = 'Alex | Lead Guitar — Voice 1'
    doc['tracks'].append({**deepcopy(doc['tracks'][0]), 'id': 1,
                          'name': 'Alex | Lead Guitar — Voice 2'})
    doc['parts'].append({'measures': [measure(rest()), measure(beat(12)), measure(rest()), measure(rest())]})
    on, off = tmp_path/'on', tmp_path/'off'
    on.mkdir(); off.mkdir()
    *_, archive, report = build(on, doc, overrides={'mainTrackId': '0'})
    assert report['status'] == 'passed', report
    *_, original, baseline = build(off, doc, enabled=False)
    assert baseline['status'] == 'passed', baseline
    with ZipFile(archive) as hybrid, ZipFile(original) as single:
        for name in single.namelist():
            if name.startswith(('arrangements/', 'notation/')):
                assert hybrid.read(name) == single.read(name), name
        receipt = json.loads(hybrid.read('import/hybrid-lead.json'))
        assert receipt['coverage']['status'] == 'complete'
