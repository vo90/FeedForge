"""Explicit pickups: known musical coordinates and independent tamper checks."""
from copy import deepcopy
from fractions import Fraction as F
import json
from zipfile import ZipFile

import pytest
import yaml

from test_song_import_score import raw_score, measure
from feedback_converter.song_import.model import ScoreImportError
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.verify_source import songsterr as read_source
from feedback_converter.song_import.verify_timeline import expected
from feedback_converter.song_import.score import load_performance
from feedback_converter.song_import.synchronization import align_from_songsterr
from feedback_converter.song_import.audio import ImportFailure
from feedback_converter.song_import.builder import build_feedpak
from feedback_converter.song_import.verification import verify_import


def beat(fret=3, length=(1, 8), **kw):
    return {'duration': list(length), 'type': length[1], 'notes': [{'string': 5, 'fret': fret}], **kw}


def document():
    raw = raw_score([measure(beat(3), beat(5), beat(7)), measure(beat(9, (1, 1)))])
    raw['parts'][0]['anacrusis'] = True
    return raw


def source_file(tmp_path, raw):
    p = tmp_path / 'source.json'
    p.write_text(json.dumps(raw), encoding='utf8')
    return p


def test_known_three_eighth_pickup_keeps_meter_and_written_rhythm():
    raw = document(); original = deepcopy(raw)
    s = parse(raw); p = render(s); independent = read_source(raw)
    assert raw == original
    assert s.measures[0].length == independent.bars[0].length == F(3, 2)
    assert s.measures[0].pickup and independent.bars[0].pickup
    assert (s.measures[0].numerator, s.measures[0].denominator) == (4, 4)
    assert [n['t'] for n in p['tracks'][0]['notes']] == [0, .25, .5, .75]
    assert [n['sus'] for n in p['tracks'][0]['notes']] == [.25, .25, .25, 2]
    assert p['beats'][0] == {'time': .25, 'measure': -1}
    assert p['beats'][1]['time'] == .75 and p['beats'][1]['measure'] > 0
    notation = p['tracks'][0]['notation']
    assert notation['measures'][0]['pickup'] is True
    assert 'pickup' not in notation['measures'][1]
    assert notation['measures'][0]['end_time'] == .75
    assert [b['dur'] for b in notation['measures'][0]['staves']['staff']['voices'][0]['beats']] == [8, 8, 8]
    wanted = expected(independent, {'offset': 0, 'scale': 1})
    assert [n['note']['t'] for n in wanted['parts'][0]['notes']] == [0, .25, .5, .75]


@pytest.mark.parametrize('flag', [False, None])
def test_unmarked_underfilled_bar_is_not_guessed(flag):
    raw = document()
    if flag is None: raw['parts'][0].pop('anacrusis')
    else: raw['parts'][0]['anacrusis'] = flag
    assert parse(raw).measures[0].length == read_source(raw).bars[0].length == 4
    assert not parse(raw).measures[0].pickup


def test_full_flagged_and_partial_final_bars_are_not_shortened():
    raw = document(); raw['parts'][0]['measures'].reverse()
    for s in (parse(raw).measures, read_source(raw).bars):
        assert [b.length for b in s] == [4, 4]
        assert not s[0].pickup


@pytest.mark.parametrize('bad', [1, 0, None, 'true', [], {}])
def test_invalid_flags_are_rejected_independently(bad):
    raw = document(); raw['parts'][0]['anacrusis'] = bad
    with pytest.raises(ScoreImportError): parse(raw)
    with pytest.raises(ValueError): read_source(raw)


@pytest.mark.parametrize('kind', ['flag', 'duration', 'empty', 'overfull', 'bad_grace', 'zero_duration'])
def test_conflicts_are_not_repaired(kind):
    raw = document()
    if kind in ('flag', 'duration'):
        raw['tracks'].append({**raw['tracks'][0], 'id': 1})
        raw['parts'].append(deepcopy(raw['parts'][0]))
        if kind == 'flag': raw['parts'][1].pop('anacrusis')
        else: raw['parts'][1]['measures'][0]['voices'][0]['beats'].pop()
    else:
        b = raw['parts'][0]['measures'][0]['voices'][0]['beats']
        if kind == 'empty': b.clear()
        elif kind == 'overfull': b[0]['duration'] = [2, 1]
        elif kind == 'bad_grace': b[0]['graceNote'] = 'unknown'
        else: b[0]['duration'] = [0, 1]
    with pytest.raises(ScoreImportError): parse(raw)
    with pytest.raises(ValueError): read_source(raw)


def test_longest_voice_rest_parts_and_whole_rest_meter():
    raw = document()
    raw['parts'][0]['measures'][0]['voices'].append({'beats': [beat(10)]})
    raw['tracks'].append({**raw['tracks'][0], 'id': 1})
    other = deepcopy(raw['parts'][0]); raw['parts'].append(other)
    other['measures'][0]['voices'] = [{'beats': [{'type': 4, 'dots': 1, 'duration': [3, 8], 'rest': True, 'notes': [{'rest': True}]}]}]
    assert parse(raw).measures[0].length == read_source(raw).bars[0].length == F(3, 2)
    raw = document()
    raw['parts'][0]['measures'][0] = measure({'type': 1, 'duration': [1, 1], 'rest': True, 'notes': [{'rest': True}]}, signature=[3, 4])
    assert parse(raw).measures[0].length == read_source(raw).bars[0].length == 3


@pytest.mark.parametrize('grace', ['onBeat', 'beforeBeat'])
def test_grace_borrows_time_and_swing_preserves_pickup_extent(grace):
    raw = document(); first = raw['parts'][0]['measures'][0]
    first['tripletFeel'] = '8th'
    first['voices'][0]['beats'].insert(0, beat(2, (1, 32), graceNote=grace))
    s = parse(raw); independent = read_source(raw)
    p = render(s); w = expected(independent, {'offset': 0, 'scale': 1})
    assert s.measures[0].length == independent.bars[0].length == F(3, 2)
    assert [n['t'] for n in p['tracks'][0]['notes']] == pytest.approx([n['note']['t'] for n in w['parts'][0]['notes']], abs=1e-6)
    assert p['tracks'][0]['notes'][-1]['t'] == .75


def test_tie_into_next_bar_remains_continuous():
    raw = document()
    first = raw['parts'][0]['measures'][0]['voices'][0]['beats']
    first[:] = [beat(3, (1, 8))]
    raw['parts'][0]['measures'][1]['voices'][0]['beats'] = [beat(3, (1, 1))]
    raw['parts'][0]['measures'][1]['voices'][0]['beats'][0]['notes'][0]['tie'] = True
    p = render(parse(raw)); w = expected(read_source(raw), {'offset': 0, 'scale': 1})
    assert p['tracks'][0]['notes'][0]['sus'] == 2.25
    assert w['parts'][0]['notes'][0]['note']['sus'] == 2.25


def test_repeat_visits_and_within_pickup_tempo_coordinates():
    raw = document(); part = raw['parts'][0]
    part['measures'][0]['repeatStart'] = True; part['measures'][1]['repeat'] = 2
    part['automations']['tempo'].append({'measure': 0, 'position': 960, 'bpm': 60, 'type': 4})
    p = render(parse(raw)); w = expected(read_source(raw), {'offset': 0, 'scale': 1})
    assert [m['quarters'] for m in p['scoreTimeline']['measures']] == [1.5, 4, 1.5, 4]
    assert [n['t'] for n in p['tracks'][0]['notes']] == pytest.approx([n['note']['t'] for n in w['parts'][0]['notes']], abs=1e-6)
    part['automations']['tempo'][-1]['position'] = 1440
    with pytest.raises(ScoreImportError): render(parse(raw))
    with pytest.raises(ValueError): expected(read_source(raw), {'offset': 0, 'scale': 1})


def test_negative_video_start_is_still_rejected():
    raw = document(); p = render(parse(raw))
    metadata = {'songId': '123', 'revisionId': '456', 'approval': 'approved'}
    timing = {'version': 1, 'source': 'songsterr-video-points', **metadata, 'videoId': 'abcdefghijk', 'status': 'done', 'feature': None, 'points': [-.2, .9, 3.16]}
    with pytest.raises(ImportFailure) as e:
        align_from_songsterr(p, {'duration': 4, 'source': {'kind': 'youtube', 'videoId': timing['videoId']}}, timing, metadata)
    assert e.value.diagnostics['sourceSyncReason'] == 'negative_note_time'


@pytest.fixture
def packaged(tmp_path):
    from test_song_import_builder import inputs
    _, audio, _, job = inputs(tmp_path)
    source = source_file(tmp_path, document()); performance = load_performance(source)
    alignment = {'status': 'validated', 'offset': 1, 'scale': 1}
    result = build_feedpak(performance, audio, alignment, job, output_dir=tmp_path/'out', source_path=source,
                          recipe={'preservationContract': 34}, compatibility=performance['compatibilityReport'])
    return source, result['stagingPath'], alignment


def test_exported_pickup_package_passes_independent_checks(packaged):
    source, file, alignment = packaged
    r = verify_import(source, file, alignment)
    assert r['status'] == 'passed', r
    with ZipFile(file) as z:
        m = yaml.safe_load(z.read('manifest.yaml'))
        chart = json.loads(z.read(m['arrangements'][0]['file']))
        assert [n['t'] for n in chart['notes']] == [1, 1.25, 1.5, 1.75]
        assert chart['beats'][0] == {'time': 1.25, 'measure': -1}
        assert chart['beats'][1] == {'time': 1.75, 'measure': 1}
        assert z.read(m['song_import']['sourceFile']) == source.read_bytes()


def test_pickup_display_clock_preserves_repeat_tempo_and_map_breaks(tmp_path):
    from feedback_converter.song_import.pickup_archive import archive
    from feedback_converter.song_import.verify_pickup import verify
    from feedback_converter.song_import.verification import Check
    import hashlib
    raw = document(); part = raw['parts'][0]
    part['measures'][0]['repeatStart'] = True; part['measures'][1]['repeat'] = 2
    part['automations']['tempo'].append({'measure': 0, 'position': 960, 'bpm': 60, 'type': 4})
    file = source_file(tmp_path, raw); p = render(parse(raw))
    alignment = {'mapping':'piecewise-linear', 'anchors':[
        {'score':0,'audio':1}, {'score':.25,'audio':1.4}, {'score':p['duration'],'audio':p['duration']+1}]}
    evidence = archive(p, alignment, file)
    check = Check(); verify(read_source(raw), alignment, evidence, hashlib.sha256(file.read_bytes()).hexdigest(), check)
    assert not check.errors, check.errors
    assert [r['occurrence'] for r in evidence['measures']] == [0,2]
    assert [a['quarter'] for a in evidence['measures'][0]['anchors']] == [0,.5,1,1.5]


def test_pickup_clock_does_not_duplicate_float_bar_boundary(tmp_path):
    from feedback_converter.song_import.pickup_archive import archive
    from feedback_converter.song_import.verify_pickup import verify
    from feedback_converter.song_import.verification import Check
    import hashlib
    raw=document();raw['parts'][0]['automations']['tempo'][0]['bpm']=123
    file=source_file(tmp_path,raw);p=render(parse(raw));end=p['scoreTimeline']['measures'][0]['end']
    alignment={'mapping':'piecewise-linear','anchors':[{'score':0,'audio':.04},
        {'score':end,'audio':.8},{'score':p['duration'],'audio':3}]}
    evidence=archive(p,alignment,file);check=Check()
    verify(read_source(raw),alignment,evidence,hashlib.sha256(file.read_bytes()).hexdigest(),check)
    assert not check.errors,check.errors
    assert len(evidence['measures'][0]['anchors'])==2


@pytest.mark.parametrize('fault', ['pickup', 'false_pickup', 'length', 'attack', 'grid', 'display_clock'])
def test_package_mutations_fail_independent_verification(packaged, tmp_path, fault):
    source, file, alignment = packaged
    with ZipFile(file) as z: files = {n: z.read(n) for n in z.namelist()}
    m = yaml.safe_load(files['manifest.yaml']); arr = m['arrangements'][0]
    target = (m['song_import']['pickupTimelineFile'] if fault == 'display_clock' else
              arr['file'] if fault == 'attack' else m['song_timeline'] if fault == 'grid' else arr['notation'])
    payload = json.loads(files[target])
    if fault == 'pickup': payload['measures'][0].pop('pickup')
    elif fault == 'false_pickup': payload['measures'][1]['pickup'] = True
    elif fault == 'length': payload['measures'][0]['duration_seconds'] = 2
    elif fault == 'attack': payload['notes'][1]['t'] += .2
    elif fault == 'display_clock': payload['measures'][0]['anchors'][0]['time'] += .2
    else: payload['beats'][0]['time'] -= .25
    files[target] = json.dumps(payload).encode(); changed = tmp_path/'changed.feedpak'
    with ZipFile(changed, 'w') as z:
        for n, data in files.items(): z.writestr(n, data)
    assert verify_import(source, changed, alignment)['status'] == 'failed'
