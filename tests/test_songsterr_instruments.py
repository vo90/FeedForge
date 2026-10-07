from copy import deepcopy
import json

import pytest

from feedback_converter.song_import.songsterr_instruments import resolve, program_kind
from feedback_converter.song_import.verify_instruments import resolve as independent
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import import verify_source
from test_song_import_score import raw_score, measure, beat


def source(tuning=(41, 36, 31, 26), name='Bass Distortion', program=30):
    raw = raw_score([measure(beat(3))], list(tuning))
    raw['tracks'][0].update(name=name, instrumentId=program)
    raw['parts'][0].update(tuning=list(tuning), strings=len(tuning), instrumentId=program)
    return raw


def both(raw):
    before = deepcopy(raw)
    result = resolve(raw['tracks'], raw['parts'])
    assert result == independent(raw['tracks'], raw['parts'], [program_kind(m) for m in raw['tracks']])
    assert raw == before
    return result


@pytest.mark.parametrize('count', [4, 5])
@pytest.mark.parametrize('transpose', [-5, -2, 0, 2])
@pytest.mark.parametrize('drop', [0, 1, 2])
def test_bass_layouts_preserve_sound_and_pitch(count, transpose, drop):
    tuning = [43 + transpose - 5*i for i in range(count)]
    tuning[-1] -= drop
    raw = source(tuning)
    kinds, receipt = both(raw)
    assert kinds == ['bass']
    score = parse(raw)
    assert score.source['instrumentResolution'] == receipt
    assert score.tracks[0].instrument == score.tracks[0].role == 'bass'
    assert score.tracks[0].tuning == list(reversed(tuning))
    expected = deepcopy(raw)
    expected['tracks'][0]['name'] = 'Distorted instrument'
    old = parse(expected)
    assert score.tracks[0].tone_source == old.tracks[0].tone_source
    for key in ('notes', 'chords', 'templates', 'tuning', 'capo'):
        assert render(score)['tracks'][0].get(key) == render(old)['tracks'][0].get(key)
    checked = verify_source.songsterr(raw)
    assert checked.parts[0].instrument == 'bass'
    assert checked.instrument_resolution == receipt


@pytest.mark.parametrize('case', ['no_label', 'drum_label', 'clarinet_label', 'vocal_label', 'cue_label',
    'excluded_program', 'bass_program', 'vocal', 'part_program', 'different_tuning', 'string_count',
    'bool_count', 'float_pitch', 'high_register', 'low_register', 'irregular', 'six_string'])
def test_unqualified_sources_are_not_reclassified(case):
    raw = source()
    meta, part = raw['tracks'][0], raw['parts'][0]
    if case.endswith('_label'):
        meta['name'] = {'no_label':'Guitar', 'drum_label':'Bass drum', 'clarinet_label':'Bass Clarinet',
                        'vocal_label':'Bass vocals', 'cue_label':'Bass cues'}[case]
    elif case == 'excluded_program': meta['instrumentId'] = part['instrumentId'] = 71
    elif case == 'bass_program': meta['instrumentId'] = part['instrumentId'] = 34
    elif case == 'vocal': meta['isVocalTrack'] = True
    elif case == 'part_program': part['instrumentId'] = 29
    elif case == 'different_tuning': meta['tuning'][0] += 1
    elif case == 'string_count': part['strings'] = 5
    elif case == 'bool_count': part['strings'] = True
    elif case == 'float_pitch': meta['tuning'][0] = part['tuning'][0] = 41.0
    else:
        tuning = {'high_register':[55,50,45,40], 'low_register':[37,32,27,22],
                  'irregular':[41,36,32,26], 'six_string':[48,43,38,33,28,23]}[case]
        meta['tuning'] = tuning; part.update(tuning=tuning, strings=len(tuning))
    kinds, receipt = both(raw)
    assert kinds == [program_kind(meta)] and receipt['tracks'] == []


def test_peer_can_corroborate_but_resolution_cannot_bootstrap_other_guitars():
    raw = source(name='Distorted part')
    raw['tracks'].append({**deepcopy(raw['tracks'][0]), 'id': 1, 'name':'Water', 'instrumentId':34})
    raw['parts'].append({**deepcopy(raw['parts'][0]), 'instrumentId':34})
    kinds, receipt = both(raw)
    assert kinds == ['bass','bass'] and receipt['tracks'][0]['bassPeerIds'] == ['1']
    raw['tracks'][1]['instrumentId'] = raw['parts'][1]['instrumentId'] = 30
    raw['tracks'][1]['name'] = 'Bass distortion'
    assert both(raw)[0] == ['guitar','bass']
    raw['tracks'][1]['instrumentId'] = 34
    assert both(raw)[0] == ['guitar','bass']  # contradictory peer sound metadata is insufficient


def test_old_contract_interpretation_remains_available():
    raw = source()
    assert verify_source.songsterr(raw, resolve_instruments=False).parts[0].instrument == 'guitar'


def test_export_and_independent_receipt_validation(tmp_path):
    from feedback_converter.song_import import load_performance
    from feedback_converter.song_import.builder import build_feedpak
    from feedback_converter.song_import.verification import VERSION, verify_import
    from test_song_import_builder import inputs
    from test_pending_slide_tie_verification import read_archive, write_archive
    raw = source()
    path = tmp_path/'score.json'; path.write_text(json.dumps(raw))
    performance = load_performance(path)
    _, audio, _, job = inputs(tmp_path)
    alignment = {'status':'validated', 'offset':0, 'scale':1}
    built = build_feedpak(performance, audio, alignment, job, output_dir=tmp_path/'out',
                         source_path=path, recipe={'preservationContract':VERSION},
                         compatibility=performance['compatibilityReport'])
    from pathlib import Path
    archive = Path(built['stagingPath'])
    report = verify_import(path, archive, alignment)
    assert report['status'] == 'passed', report
    files, manifest = read_archive(archive)
    assert manifest['arrangements'][0]['type'] == 'bass'
    assert manifest['arrangements'][0]['tuning'] == [-2]*4
    manifest['song_import']['instrumentResolution']['tracks'][0]['originalProgram'] = 34
    bad = verify_import(path, write_archive(tmp_path, files, manifest), alignment)
    assert bad['status'] == 'failed' and 'instrument_resolution' in {e['code'] for e in bad['errors']}


def test_resolved_bass_cannot_supply_hybrid_guitar_passages(tmp_path):
    from feedback_converter.song_import.hybrid_lead import plan
    from test_songsterr_hybrid_lead import prepared, song
    raw = song()
    bass = source()
    raw['tracks'].append({**bass['tracks'][0], 'id': 'distorted-bass'})
    body = deepcopy(bass['parts'][0])
    body['measures'] *= len(raw['parts'][0]['measures'])
    raw['parts'].append(body)
    _, performance, options = prepared(tmp_path, raw)
    result = plan(performance, options, options['mainTrackId'],
                  {'status':'validated','offset':0,'scale':1}, 20)
    assert {'trackId':'distorted-bass', 'reason':'not_guitar'} in result['excluded']
    assert all(p['trackId'] != 'distorted-bass' for p in result['passages'])


def test_verifier_does_not_call_production_resolver(monkeypatch):
    from feedback_converter.song_import import songsterr_instruments
    def forbidden(*args):
        raise AssertionError('Verifier reused production instrument inference')
    monkeypatch.setattr(songsterr_instruments, 'resolve', forbidden)
    checked = verify_source.songsterr(source())
    assert checked.parts[0].instrument == 'bass'
