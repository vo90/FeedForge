"""Unpitched source strikes keep their identity without a fabricated fret."""
from copy import deepcopy
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from feedback_converter.feedpak_semantics import validate_arrangement_semantics
from feedback_converter.feedpak_validator import validate_feedpak
from feedback_converter.song_import.builder import build_feedpak
from feedback_converter.song_import.model import ScoreImportError
from feedback_converter.song_import.score import load_performance
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected
from feedback_converter.song_import.verification import verify_import
from test_song_import_score import raw_score, measure, beat, import_json
from test_song_import_builder import inputs


def source():
    # A fretted note, a dead strike + continuation, then a mixed chord.
    bars = [measure(beat(fret=7, duration=(1, 4)),
                    beat(fret=None, duration=(1, 4), dead=True, ghost=True),
                    beat(fret=None, duration=(1, 4), dead=True, tie=True),
                    {'duration': [1, 4], 'notes': [
                        {'string': 0, 'dead': True, 'accentuated': True},
                        {'string': 1, 'fret': 5}]})]
    return raw_score(bars)


def flat(track):
    return track['notes'] + [{**n, 't': c['t']} for c in track['chords'] for n in c['notes']]


def test_unpitched_strikes_ties_and_mixed_chords_retain_exact_source_identity(tmp_path):
    doc = source(); original = deepcopy(doc)
    performance = import_json(tmp_path, doc)
    track = performance['tracks'][0]
    notes = flat(track)
    assert [(n['t'], n['s'], n['f'], n['sus']) for n in notes] == [
        (0, 5, 7, .5), (.5, 5, 127, 1), (1.5, 4, 5, .5), (1.5, 5, 127, .5)]
    assert notes[1]['mt'] and notes[1]['ghost'] and notes[3]['mt'] and notes[3]['ac']
    assert len(notes[1]['source_ids']) == 2
    assert track['templates'][0]['frets'] == [-1, -1, -1, -1, 5, 127]
    assert 'notation' not in track and doc == original
    report = performance['compatibilityReport']
    assert report['status'] == 'limitations'
    assert report['findings'][0]['feature'] == 'notation.unpitched_mute'
    assert report['findings'][0]['workStatus'] == 'display_limitation'
    independent = expected(songsterr(doc), {'offset': 0, 'scale': 1})
    assert len(independent['parts'][0]['notes']) == 4
    written = [n for b in independent['parts'][0]['notation_beats'] for n in b['notes']]
    assert all('midi' not in n for n in written if n['fret'] == 127)


@pytest.mark.parametrize('note', [
    {'string': 0}, {'string': 0, 'fret': None, 'dead': False},
    {'string': 0, 'fret': None, 'dead': 'yes'}, {'string': 0, 'fret': 127, 'dead': True},
    {'string': 0, 'fret': -1, 'dead': True}])
def test_missing_or_invalid_pitched_fret_is_not_reclassified_as_a_mute(tmp_path, note):
    doc = raw_score([measure({'duration': [1, 1], 'notes': [note]})])
    with pytest.raises(ScoreImportError):
        import_json(tmp_path, doc)
    with pytest.raises((KeyError, ValueError)):
        songsterr(doc)


@pytest.mark.parametrize('gesture', ['hp', 'slide', 'bend', 'harmonic'])
def test_pitch_gestures_on_unpitched_strikes_remain_explicitly_unsupported(tmp_path, gesture):
    doc = source(); doc['parts'][0]['measures'][0]['voices'][0]['beats'][1]['notes'][0][gesture] = True
    with pytest.raises(ScoreImportError, match='support'):
        import_json(tmp_path, doc)


@pytest.mark.parametrize('gesture', ['hp', 'slide'])
def test_pitched_link_cannot_invent_a_fret_for_its_unpitched_destination(tmp_path, gesture):
    doc = source(); doc['parts'][0]['measures'][0]['voices'][0]['beats'][0]['notes'][0][gesture] = True if gesture == 'hp' else 'shift'
    with pytest.raises(ScoreImportError, match='unpitched'):
        import_json(tmp_path, doc)
    with pytest.raises(ValueError, match='unpitched'):
        expected(songsterr(doc), {'offset': 0, 'scale': 1})


def semantics(chart):
    errors = []
    validate_arrangement_semantics(chart, {}, 'chart', errors.append)
    return errors


def test_sentinel_validation_requires_mute_evidence_on_every_template_use():
    chart = {'notes': [{'t': 0, 's': 0, 'f': 127, 'mt': True}],
             'templates': [{'frets': [127, -1, -1, -1, -1, -1]}],
             'chords': [{'t': 1, 'id': 0, 'notes': [{'s': 0, 'f': 127, 'mt': True}]}]}
    assert not semantics(chart)
    for field in ('mt', 'pm', 'fhm'):
        bad = deepcopy(chart); bad['notes'][0].pop('mt'); bad['notes'][0][field] = False if field == 'mt' else True
        assert semantics(bad)
    for replacement in ([], [{'s': 0, 'f': 0, 'mt': True}], [{'s': 0, 'f': 127}]):
        bad = deepcopy(chart); bad['chords'][0]['notes'] = replacement
        assert semantics(bad)
    bad = deepcopy(chart); bad['chords'] = []
    assert semantics(bad), 'unreferenced sentinel templates are not proven'
    bad = deepcopy(chart)
    bad['phrases'] = [{'start_time': 0, 'end_time': 2, 'max_difficulty': 0,
                      'levels': [{'difficulty': 0, 'chords': [{'t': 1, 'id': 0}]}]}]
    assert semantics(bad), 'a template-only practice chord must not invent a pitch'


@pytest.mark.parametrize('fault', [None, 'fret', 'mute', 'string', 'duration', 'missing', 'notation'])
def test_packaged_tab_is_independently_verified_and_corruption_is_rejected(tmp_path, fault):
    _, audio, _, job = inputs(tmp_path)
    path = tmp_path / 'source.json'; path.write_text(json.dumps(source()), encoding='utf-8')
    performance = load_performance(path)
    alignment = {'status': 'validated', 'offset': 0, 'scale': 1}
    result = build_feedpak(performance, audio, alignment, job, output_dir=tmp_path / 'library',
                          source_path=path, compatibility=performance['compatibilityReport'], recipe={'preservationContract': 9})
    archive = Path(result['stagingPath'])
    assert validate_feedpak(archive).ok
    verified = verify_import(path, archive, alignment)
    assert verified['status'] == 'passed', verified
    with ZipFile(archive) as z:
        files = {n: z.read(n) for n in z.namelist()}
    manifest = yaml.safe_load(files['manifest.yaml']); name = manifest['arrangements'][0]['file']
    assert 'notation' not in manifest['arrangements'][0]
    chart = json.loads(files[name]); mute = chart['notes'][1]
    if fault == 'fret': mute['f'] = 7
    elif fault == 'mute': mute.pop('mt')
    elif fault == 'string': mute['s'] = 1
    elif fault == 'duration': mute['sus'] = .5
    elif fault == 'missing': chart['notes'].pop(1)
    elif fault == 'notation':
        manifest['arrangements'][0]['notation'] = 'invented.json'
        files['invented.json'] = b'{}'
        files['manifest.yaml'] = yaml.safe_dump(manifest).encode()
    files[name] = json.dumps(chart).encode()
    changed = tmp_path / 'changed.feedpak'
    with ZipFile(changed, 'w') as z:
        for name, value in files.items(): z.writestr(name, value)
    check = verify_import(path, changed, alignment)
    assert check['status'] == ('passed' if fault is None else 'failed'), check
