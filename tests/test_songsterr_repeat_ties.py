from copy import deepcopy
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from test_song_import_score import beat, measure, raw_score
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected
from feedback_converter.song_import.verification import verify_import
from test_songsterr_hybrid_lead import build

FIXTURE = json.loads((Path(__file__).parent/'fixtures/songsterr_repeat_tie_reference.json').read_text())


def both(doc):
    actual = render(parse(doc))
    independent = expected(songsterr(doc), {'offset': 0, 'scale': 1})
    return actual, independent


@pytest.mark.parametrize('case', FIXTURE['cases'])
def test_repeat_tie_matches_pinned_native_tie_stage(case):
    assert FIXTURE['referenceSha256'] == '4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab'
    doc = raw_score([])
    doc['parts'][0] = deepcopy(case['input'])
    doc['tracks'][0].update(instrumentId=case['input']['instrumentId'], tuning=case['input']['tuning'])
    before = deepcopy(doc)
    actual, independent = both(doc)
    track = actual['tracks'][0]
    notes = track['notes'] + [{**n, 't': chord['t']} for chord in track['chords'] for n in chord['notes']]
    wanted = independent['parts'][0]['notes']
    assert len(notes) == len(wanted) == len(case['expected'])
    by_string = {n['s']: n for n in notes}
    independent_by_string = {n['note']['s']: n['note'] for n in wanted}
    for ref in case['expected']:
        string = len(case['input']['tuning']) - 1 - ref['string']
        n, v = by_string[string], independent_by_string[string]
        assert n['f'] == v['f'] == ref['fret']
        assert n['t'] == v['t'] == ref['attackTick'] / case['tpqn'] / 2
        assert n['sus'] == v['sus'] == (ref['endTick'] + 1 - ref['attackTick']) / case['tpqn'] / 2
        assert len(n['source_ids']) == len(case['traversal'])
    assert doc == before


@pytest.mark.parametrize('fault', ['gap', 'rest', 'pitch', 'voice', 'missing', 'delayed', 'slide', 'hopo'])
def test_repeat_boundary_does_not_invent_or_repair_a_continuation(fault):
    # The first visit is valid; the repeat boundary is the counterexample.
    entrance = measure(beat(3, duration=(1, 2), tie=True), beat(3, duration=(1, 2)), repeatStart=True, repeat=2)
    doc = raw_score([measure(beat(3)), entrance])
    tail = entrance['voices'][0]['beats'][1]
    if fault == 'gap': tail['duration'] = [1, 4]
    elif fault == 'rest': tail['notes'] = [{'rest': True}]
    elif fault == 'pitch': tail['notes'][0]['fret'] = 4
    elif fault == 'voice':
        tail['notes'] = [{'rest': True}]
        entrance['voices'].append({'beats': [beat(3)]})
    elif fault == 'missing': doc['parts'][0]['measures'][0]['voices'][0]['beats'][0]['notes'] = [{'rest': True}]
    elif fault == 'delayed':
        entrance['voices'][0]['beats'] = [{'duration':[1,4], 'notes':[{'rest':True}]}, beat(3, duration=(1,4), tie=True), beat(3,duration=(1,2))]
    elif fault == 'slide': tail['notes'][0]['slide'] = 'shift'
    elif fault == 'hopo': tail['notes'][0]['hp'] = True
    for reader in (lambda: render(parse(doc)), lambda: expected(songsterr(doc), {'offset':0,'scale':1})):
        with pytest.raises(ValueError): reader()


def test_explicit_repeated_attacks_remain_attacks():
    doc = raw_score([measure(beat(3)), measure(beat(3), repeatStart=True, repeat=3)])
    actual, independent = both(doc)
    assert [n['t'] for n in actual['tracks'][0]['notes']] == [0, 2, 4, 6]
    assert [n['note']['t'] for n in independent['parts'][0]['notes']] == [0, 2, 4, 6]


@pytest.mark.parametrize('fault', [None, 'short_sustain', 'extra_attack', 'changed_pitch', 'lost_tie_notation'])
def test_hybrid_package_preservation_and_independent_mutation_checks(tmp_path, fault):
    doc = raw_score([measure(beat(3)), measure(beat(3, tie=True), repeatStart=True, repeat=2)])
    hybrid = fault is None
    source, _, options, alignment, archive, report = build(tmp_path, doc, difficulty=True, enabled=hybrid)
    assert report['status'] == 'passed', report
    with ZipFile(archive) as z:
        files = {n: z.read(n) for n in z.namelist()}
    manifest = yaml.safe_load(files['manifest.yaml'])
    assert json.loads(files[manifest['song_import']['sourceFile']]) == doc
    assert len(manifest['arrangements']) == (2 if hybrid else 1)
    chart_name = manifest['arrangements'][0]['file']
    chart = json.loads(files[chart_name])
    assert len(chart['notes']) == 1 and chart['notes'][0]['sus'] == 6
    if fault == 'short_sustain': chart['notes'][0]['sus'] = 2
    elif fault == 'extra_attack': chart['notes'].append({**chart['notes'][0], 't':2, 'sus':2})
    elif fault == 'changed_pitch': chart['notes'][0]['f'] = 4
    elif fault == 'lost_tie_notation':
        name = manifest['arrangements'][0]['notation']
        notation = json.loads(files[name])
        notation['measures'][1]['staves']['staff']['voices'][0]['beats'][0]['notes'][0].pop('tied')
        files[name] = json.dumps(notation).encode()
    if fault is not None:
        files[chart_name] = json.dumps(chart).encode()
    candidate = tmp_path/'checked.feedpak'
    with ZipFile(candidate, 'w') as z:
        for name, data in files.items(): z.writestr(name, data)
    result = verify_import(source, candidate, alignment, hybrid_options=options if hybrid else {'enabled': False})
    assert result['status'] == ('passed' if fault is None else 'failed'), result
    if fault:
        code = {'short_sustain':'note_sustain', 'extra_attack':'note_count', 'changed_pitch':'note_f',
                'lost_tie_notation':'notation_note'}[fault]
        assert any(e['code'].startswith(code) for e in result['errors']), result
