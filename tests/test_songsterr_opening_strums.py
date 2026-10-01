"""Authored anticipations retain music and require real recording coverage."""
from copy import deepcopy
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from test_song_import_score import beat, measure, raw_score, import_json
from test_song_import_builder import inputs
from feedback_converter.song_import.audio import ImportFailure
from feedback_converter.song_import.builder import build_feedpak
from feedback_converter.song_import.preparation import finalize
from feedback_converter.song_import.synchronization import align_from_songsterr, map_source_time
from feedback_converter.song_import.verification import verify_import
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected

META = {'songId': '123', 'revisionId': '456', 'approval': 'approved'}
VIDEO = 'abcdefghijk'


def document(kind='arpeggio', direction='down', shift=8, repeat=False):
    first = beat(duration=(1, 4))
    first['notes'] = [{'string': s, 'fret': f} for s, f in [(3, 3), (4, 2), (5, 0)]]
    first[kind] = {'direction': direction, 'duration': 74, 'shift': shift}
    raw = raw_score([measure(first, beat(duration=(3, 4)), **({'repeatStart': True, 'repeat': 2} if repeat else {})),
                     measure(beat())])
    raw['parts'][0]['automations']['tempo'] = [
        {'measure': 0, 'position': 0, 'bpm': 76, 'type': 4},
        {'measure': 1, 'position': 0, 'bpm': 194, 'type': 4}]
    return raw


def timing(points=(.19, 3.48, 4.8), **fields):
    return {'version': 1, 'source': 'songsterr-video-points', **META,
            'status': 'done', 'feature': None, 'videoId': VIDEO, 'points': list(points), **fields}


def align(p, sync=None):
    return align_from_songsterr(p, {'duration': 8, 'source': {'kind': 'youtube', 'videoId': VIDEO}},
                               sync or timing(), META)


def assert_groups(actual, wanted):
    assert len(actual) == len(wanted)
    for a, w in zip(actual, wanted):
        assert {k: v for k, v in a.items() if k not in ('notes', 'time')} == {k: v for k, v in w.items() if k not in ('notes', 'time')}
        assert a['time'] == pytest.approx(w['time'], abs=1e-10)
        assert len(a['notes']) == len(w['notes'])
        for an, wn in zip(a['notes'], w['notes']):
            assert (an['s'], an['f']) == (wn['s'], wn['f'])
            assert an['t'] == pytest.approx(wn['t'], abs=1e-10)


@pytest.mark.parametrize('kind', ['brushStroke', 'arpeggio'])
@pytest.mark.parametrize('direction', ['up', 'down'])
@pytest.mark.parametrize('shift', [0, 8, 46, 100])
def test_opening_clock_and_string_spacing_match_independent_evaluator(tmp_path, kind, direction, shift):
    raw = document(kind, direction, shift)
    original = deepcopy(raw)
    p = import_json(tmp_path, raw)
    a = align(p)
    independent = expected(songsterr(raw), a)
    assert_groups(p['strumEvidence'], independent['strums'])
    early = min(n['t'] for n in p['tracks'][0]['notes'])
    assert early == pytest.approx(-74 / 1440 * 2 * (100-shift) / 100 * 60 / 76)
    if shift == 100:
        assert 'openingStrum' not in a['provenance']
    else:
        assert a['provenance']['openingStrum']['scoreStart'] == early
        assert map_source_time(a, early) > 0
        with pytest.raises(ImportFailure):
            map_source_time(a, early-.001)
    assert raw == original
    assert a['anchors'] == [{'score': 0, 'audio': .19, 'quarter': 0},
                            {'score': 4*60/76, 'audio': 3.48, 'quarter': 4},
                            {'score': 4*60/76+4*60/194, 'audio': 4.8, 'quarter': 8}]


@pytest.mark.parametrize('fault', ['before_audio', 'missing_map', 'revision', 'recording', 'non_increasing', 'unsupported_event'])
def test_opening_does_not_weaken_recording_or_source_guards(tmp_path, fault):
    p = import_json(tmp_path, document())
    sync = timing()
    if fault == 'before_audio': sync['points'][0] = 0
    if fault == 'missing_map': sync['status'] = 'unavailable'
    if fault == 'revision': sync['revisionId'] = '457'
    if fault == 'recording': sync['videoId'] = 'zyxwvutsrqp'
    if fault == 'non_increasing': sync['points'][1] = 0
    if fault == 'unsupported_event': p['tracks'][0]['notes'][0]['t'] -= 1
    with pytest.raises(ImportFailure):
        align(p, sync)


def test_repeat_of_opening_strum_keeps_each_occurrence(tmp_path):
    raw = document(repeat=True)
    p = import_json(tmp_path, raw)
    a = align(p, timing((.19, 3.48, 6.8, 8)))
    independent = expected(songsterr(raw), a)
    assert_groups(p['strumEvidence'], independent['strums'])
    assert [g['occurrence'] for g in p['strumEvidence']] == [1, 2]
    assert len(a['provenance']['openingStrum']['groups']) == 1


@pytest.mark.parametrize('other_fret', [3, 9])
def test_combined_and_separate_voices_have_independently_verified_openings(tmp_path, other_fret):
    from feedback_converter.song_import.verification import Check
    from feedback_converter.song_import.verify_synchronization import verify_source_timing
    raw = document()
    voices = raw['parts'][0]['measures'][0]['voices']
    second = deepcopy(voices[0])
    second['beats'][0]['notes'][0]['fret'] = other_fret
    voices.append(second)
    p = import_json(tmp_path, raw)
    a = align(p)
    source = songsterr(raw)
    independent = expected(source, a)
    assert_groups(p['strumEvidence'], independent['strums'])
    check = Check()
    verify_source_timing(source, a, {'audioSource': {'kind': 'youtube', 'videoId': VIDEO},
                                    'alignment': {'provenance': a['provenance']}},
                         a['sourceTiming'], check, strums=independent['strums'])
    assert not check.errors, check.errors


def test_six_string_arpeggio_preserves_its_recording_start(tmp_path):
    raw = document()
    raw['parts'][0]['automations']['tempo'][0]['bpm'] = 94
    first = raw['parts'][0]['measures'][0]['voices'][0]['beats'][0]
    first['notes'] = [{'string': s, 'fret': f} for s, f in enumerate([3, 0, 0, 0, 2, 3])]
    first['arpeggio'].update(duration=162, shift=46)
    p = import_json(tmp_path, raw)
    a = align(p, timing((1.46, 4.01, 5.5)))
    assert map_source_time(a, a['provenance']['openingStrum']['scoreStart']) == pytest.approx(1.363180, abs=1e-6)
    assert a['provenance']['openingStrum']['noteCount'] == 3


@pytest.fixture
def package(tmp_path):
    raw = document('brushStroke')
    p = import_json(tmp_path, raw)
    source = tmp_path/'score.json'
    source.write_text(json.dumps(raw), encoding='utf-8')
    _, audio, _, job = inputs(tmp_path)
    audio['source'] = {'kind': 'youtube', 'videoId': VIDEO}
    a = align(p)
    audio, a = finalize(p, audio, a, job)
    recipe = {'preservationContract': 41, 'audioSource': audio['source'],
              'alignment': {'provenance': deepcopy(a['provenance'])}, 'preparation': a['preparation']}
    built = build_feedpak(p, audio, a, job, output_dir=tmp_path/'out', source_path=source,
                         recipe=recipe, compatibility=p['compatibilityReport'])
    return source, Path(built['stagingPath']), a


def test_complete_archive_preserves_opening_and_two_second_preparation(package):
    source, archive, a = package
    result = verify_import(source, archive, a, META)
    assert result['status'] == 'passed', result
    with ZipFile(archive) as z:
        manifest = yaml.safe_load(z.read('manifest.yaml'))
        chart = json.loads(z.read(manifest['arrangements'][0]['file']))
        assert min(n['t'] for n in chart['notes']) == pytest.approx(2, abs=1/22050)
        assert len([n for n in chart['notes'] if 'ch' in n]) == 3
        assert len({n['t'] for n in chart['notes'] if 'ch' in n}) == 3
    assert a['preparation']['seconds'] == pytest.approx(1.8878, abs=1/22050)


@pytest.mark.parametrize('opening_track', [0, 1])
def test_hybrid_coverage_preserves_negative_source_positions(tmp_path, opening_track):
    from test_songsterr_hybrid_lead import prepared
    raw = document()
    raw['tracks'].append({**deepcopy(raw['tracks'][0]), 'id': 1, 'name': 'Rhythm Guitar'})
    raw['parts'].append(deepcopy(raw['parts'][0]))
    raw['parts'][1-opening_track]['measures'][0]['voices'][0]['beats'][0].pop('arpeggio')
    source, p, options = prepared(tmp_path, raw)
    options['mainTrackId'] = '0'
    _, audio, _, job = inputs(tmp_path)
    audio['source'] = {'kind': 'youtube', 'videoId': VIDEO}
    a = align(p)
    audio, a = finalize(p, audio, a, job)
    recipe = {'preservationContract': 41, 'source': 'songsterr', 'audioSource': audio['source'],
              'scoreHash': options['sourceSha256'], 'audioHash': audio['hash'], 'hybridLead': options,
              'alignment': {'provenance': deepcopy(a['provenance'])}, 'preparation': a['preparation']}
    built = build_feedpak(p, audio, a, job, output_dir=tmp_path/'out', source_path=source,
                         recipe=recipe, compatibility=p['compatibilityReport'],
                         hybrid_lead={'enabled': True, 'mainTrackId': '0', 'options': options})
    archive = Path(built['stagingPath'])
    result = verify_import(source, archive, a, META, hybrid_options=options)
    assert result['status'] == 'passed', result
    with ZipFile(archive) as z:
        receipt = json.loads(z.read('import/hybrid-lead.json'))
        early = [e for e in receipt['coverage']['events'] if e['start'] < 0]
        assert early and all(e['recordingStart'] >= 1.99999 for e in early)


@pytest.mark.parametrize('fault', ['missing', 'boundary', 'member_count', 'source_id', 'invented', 'note_time', 'recording_start'])
def test_forged_opening_evidence_or_note_timing_fails(package, tmp_path, fault):
    source, archive, original = package
    a = deepcopy(original)
    with ZipFile(archive) as z:
        files = {n: z.read(n) for n in z.namelist()}
    manifest = yaml.safe_load(files['manifest.yaml'])
    receipt = a['provenance']['openingStrum']
    if fault == 'missing': a['provenance'].pop('openingStrum')
    if fault == 'boundary': receipt['scoreStart'] -= .1
    if fault == 'member_count': receipt['noteCount'] += 1
    if fault == 'source_id': receipt['groups'][0]['sourceId'] = 'songsterr:0:0:0:9'
    if fault == 'invented': receipt['extra'] = True
    if fault == 'recording_start': a['anchors'][0]['audio'] -= .2
    if fault == 'note_time':
        name = manifest['arrangements'][0]['file']
        chart = json.loads(files[name]); chart['notes'][0]['t'] += .01
        files[name] = json.dumps(chart).encode()
    manifest['song_import']['alignment']['provenance'] = deepcopy(a['provenance'])
    files['manifest.yaml'] = yaml.safe_dump(manifest).encode()
    changed = tmp_path/'changed.feedpak'
    with ZipFile(changed, 'w') as z:
        for n, b in files.items(): z.writestr(n, b)
    result = verify_import(source, changed, a, META)
    assert result['status'] == 'failed', result
