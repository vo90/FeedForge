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
from feedback_converter.song_import.verification import Check, _notes, _flatten


def muted(**kwargs):
    return beat(fret=None, dead=True, **kwargs)


def shift():
    return raw_score([measure(muted(duration=(1, 2)), muted(duration=(1, 2), tie=True, slide='shift')),
                      measure(beat(fret=7))])


def compare(doc, alignment=None):
    before = deepcopy(doc)
    actual = render(parse(doc))
    ref = expected(songsterr(doc), alignment or {'offset': 0, 'scale': 1})
    assert actual.get('mutedSlideEvidence', []) == ref['muted_slides']
    if alignment is None:
        check = Check()
        for part, track in zip(ref['parts'], actual['tracks']):
            _notes(part['notes'], _flatten(track), check, part['source'], actual['duration'])
        assert not check.errors, check.errors
    assert before == doc
    return actual, ref


@pytest.mark.parametrize('direction', ['upwards', 'downwards'])
@pytest.mark.parametrize('ghost', [False, True])
def test_directional_mutes_keep_timing_strings_and_articulation(direction, ghost):
    b = muted(slide=direction, ghost=ghost)
    b['notes'].append({'string': 1, 'dead': True, 'slide': direction})
    p, ref = compare(raw_score([measure(b)]))
    notes = p['tracks'][0]['chords'][0]['notes']
    assert len(notes) == 2
    for n in notes:
        assert n['f'] == 127 and n['mt'] and n['sus'] == 2
        assert n['slide_out_marks'] == [{'direction': 'up' if direction == 'upwards' else 'down', 'start': 0, 'end': 2}]
        assert not any(k in n for k in ('sl', 'slu', 'bn', 'ho', 'po', 'pick_scrape_marks'))
    assert bool(next(n for n in notes if n['s']==5).get('ghost')) == ghost
    assert all(r['target'] is None for r in ref['muted_slides'])


def test_tied_shift_retains_relationship_without_path_or_extra_attack():
    p, _ = compare(shift())
    notes = p['tracks'][0]['notes']
    assert [(n['f'], n['t'], n['sus']) for n in notes] == [(127, 0, 2), (7, 2, 2)]
    assert notes[0]['mt'] and not notes[1].get('mt')
    assert not any(k in notes[0] for k in ('sl', 'slu', 'slide_out', 'slide_out_marks', 'ln'))
    r = p['mutedSlideEvidence'][0]
    assert (r['attack'], r['start'], r['end']) == (0, 1, 2)
    assert r['target'] == {'sourceId': 'songsterr:0:1:0:0:0', 'time': 2, 'fret': 7}


def test_mixed_chord_keeps_real_slide_paths_and_next_attack():
    doc = shift()
    for m in doc['parts'][0]['measures']:
        for b in m['voices'][0]['beats']:
            n = b['notes'][0]
            b['notes'].append({'string': 1, 'fret': 5 if n.get('fret') == 7 else 1,
                               **{k: n[k] for k in ('tie', 'slide') if k in n}})
    p, _ = compare(doc)
    first, last = p['tracks'][0]['chords']
    assert next(n for n in first['notes'] if n['f']==1)['sl'] == 5
    assert 'sl' not in next(n for n in first['notes'] if n['f']==127)
    assert len(last['notes']) == 2 and all(not n.get('mt') for n in last['notes'])


def test_repeats_and_tied_direction_changes_keep_each_interval():
    doc = raw_score([measure(muted(duration=(1, 2), slide='downwards'),
                             muted(duration=(1, 2), tie=True, slide='upwards'), repeatStart=True, repeat=2)])
    p, _ = compare(doc)
    assert len(p['tracks'][0]['notes']) == 2
    assert [r['occurrence'] for r in p['mutedSlideEvidence']] == [1, 1, 2, 2]
    assert p['tracks'][0]['notes'][0]['slide_out_marks'] == [
        {'direction': 'down', 'start': 0, 'end': 1}, {'direction': 'up', 'start': 1, 'end': 2}]


@pytest.mark.parametrize('fields', [{'slide': 'legato'}, {'slide': 'above'}, {'slide': []},
    {'slide': 'downwards', 'hp': True}, {'slide': 'upwards', 'harmonic': 'natural'},
    {'slide': 'downwards', 'bend': {'points': [{'position': 0, 'tone': 100}]}},
    {'slide': 'shift', 'vibrato': True}])
def test_other_missing_fret_pitch_gestures_still_fail(fields):
    doc = raw_score([measure(muted(**fields))])
    with pytest.raises(ValueError): render(parse(doc))
    with pytest.raises(ValueError): expected(songsterr(doc), {'offset': 0, 'scale': 1})


@pytest.mark.parametrize('fault', ['missing', 'muted_target', 'repeat_jump'])
def test_unresolved_or_pitched_to_unpitched_links_still_fail(fault):
    doc = shift()
    bars = doc['parts'][0]['measures']
    target = bars[1]['voices'][0]['beats'][0]['notes'][0]
    if fault == 'missing': bars.pop()
    if fault == 'muted_target': target['dead'] = True
    if fault == 'repeat_jump': bars[0].update(repeatStart=True, repeat=2)
    with pytest.raises(ValueError): render(parse(doc))
    with pytest.raises(ValueError): expected(songsterr(doc), {'offset': 0, 'scale': 1})


@pytest.mark.parametrize('kind', ['shift', 'out'])
@pytest.mark.parametrize('fault', [None, 'missing', 'source', 'target', 'time', 'rule', 'report', 'pitch', 'mute', 'contract'])
def test_packaging_independently_checks_source_evidence_and_chart(tmp_path, kind, fault):
    from test_song_import_builder import inputs
    from feedback_converter.song_import import load_performance
    from feedback_converter.song_import.builder import build_feedpak
    from feedback_converter.song_import.verification import verify_import
    _, audio, _, job = inputs(tmp_path)
    doc = shift() if kind == 'shift' else raw_score([measure(muted(slide='downwards'))])
    path = tmp_path / 'source.json'; path.write_text(json.dumps(doc), encoding='utf-8')
    p = load_performance(path)
    alignment = {'status': 'validated', 'offset': .2, 'scale': 1.1}
    built = build_feedpak(p, audio, alignment, job, output_dir=tmp_path/'out', source_path=path,
                         compatibility=p['compatibilityReport'], recipe={'preservationContract': 28})
    archive = Path(built['stagingPath'])
    assert verify_import(path, archive, alignment)['status'] == 'passed'
    with ZipFile(archive) as z: files = {n: z.read(n) for n in z.namelist()}
    evidence = json.loads(files['import/muted-slides.json'])
    if fault == 'source': evidence['sourceSha256'] = '0' * 64
    if fault == 'target': evidence['gestures'][0]['target'] = {'fret': 2}
    if fault == 'time': evidence['gestures'][0]['start'] += .1
    if fault == 'rule': evidence['gestures'][0]['used']['rule'] = 'invented-path'
    files['import/muted-slides.json'] = json.dumps(evidence).encode()
    if fault == 'missing': del files['import/muted-slides.json']
    if fault == 'report':
        report = json.loads(files['import/compatibility.json'])
        report['findings'] = [r for r in report['findings'] if r['feature'] != 'note.muted_slide']
        report['findingCount'] = len(report['findings'])
        files['import/compatibility.json'] = json.dumps(report).encode()
    manifest = yaml.safe_load(files['manifest.yaml'])
    if fault == 'contract':
        manifest['song_import']['preservationContract'] = 27
        files['manifest.yaml'] = yaml.safe_dump(manifest).encode()
    if fault in ('pitch', 'mute'):
        name = manifest['arrangements'][0]['file']; chart = json.loads(files[name])
        chart['notes'][0]['sl' if fault == 'pitch' else 'mt'] = 7 if fault == 'pitch' else False
        files[name] = json.dumps(chart).encode()
    mutated = tmp_path / 'checked.feedpak'
    with ZipFile(mutated, 'w') as z:
        for name, data in files.items(): z.writestr(name, data)
    result = verify_import(path, mutated, alignment)
    assert result['status'] == ('passed' if fault is None else 'failed'), result
