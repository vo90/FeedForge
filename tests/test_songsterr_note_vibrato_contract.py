"""The archive contract selects a source interpretation, never a loose label."""
import json
from zipfile import ZipFile

import pytest
import yaml

from feedback_converter.song_import.audio import ImportFailure
from feedback_converter.song_import.builder import build_feedpak
from feedback_converter.song_import.compatibility import inspect_songsterr
from feedback_converter.song_import.songsterr import parse, NOTE_VIBRATO_POLICY, WRITTEN_BEAT_VIBRATO_POLICY
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.verification import verify_import
from test_song_import_builder import inputs
from test_song_import_score import beat, measure, raw_score


def package(tmp_path, *, contract=94, historical=False, beat_flag=True, recipe_extra=None):
    first = beat(3, duration=(1, 1))
    if beat_flag:
        first['vibrato'] = True
    document = raw_score([measure(first)])
    path = tmp_path / 'source.json'
    path.write_text(json.dumps(document), encoding='utf-8')
    score = parse(document, vibrato_policy=WRITTEN_BEAT_VIBRATO_POLICY if historical else NOTE_VIBRATO_POLICY)
    performance = render(score)
    compatibility = inspect_songsterr(document, note_owned_vibrato=not historical)
    _, audio, _, job = inputs(tmp_path)
    alignment = {'status': 'validated', 'offset': .25, 'scale': 1}
    result = build_feedpak(performance, audio, alignment, job, output_dir=tmp_path / 'out',
                          source_path=path, compatibility=compatibility,
                          recipe={'preservationContract': contract, **(recipe_extra or {})})
    return path, result['stagingPath'], alignment


@pytest.mark.parametrize('contract', [63, 77, 93])
def test_new_beat_interpretation_cannot_be_labelled_as_historical(tmp_path, contract):
    with pytest.raises(ImportFailure, match='contract 94'):
        package(tmp_path, contract=contract)


def test_historical_mode_cannot_be_labelled_as_new(tmp_path):
    with pytest.raises(ImportFailure, match='matching conversion policy'):
        package(tmp_path, historical=True)


@pytest.mark.parametrize('contract', [63, 77, 93])
def test_historical_archives_reconstruct_original_written_fallback(tmp_path, contract):
    source, archive, alignment = package(tmp_path, contract=contract, historical=True)
    result = verify_import(source, archive, alignment)
    assert result['status'] == 'passed', result
    with ZipFile(archive) as z:
        manifest = yaml.safe_load(z.read('manifest.yaml'))
        assert 'fingerVibratoPolicy' not in manifest['song_import']
        chart = json.loads(z.read(manifest['arrangements'][0]['file']))
        assert chart['notes'][0]['vb'] is True and chart['notes'][0]['vibrato_marks']


def test_no_legacy_flags_has_identical_historical_meaning(tmp_path):
    source, archive, alignment = package(tmp_path, contract=93, beat_flag=False)
    assert verify_import(source, archive, alignment)['status'] == 'passed'


@pytest.mark.parametrize('fault', ['missing', 'wrong', 'downgrade', 'downgrade_without_policy', 'bool', 'future', 'old_inventory', 'invented_vb', 'invented_marks'])
def test_new_policy_requires_binding_and_rejects_invented_controller(tmp_path, fault):
    source, archive, alignment = package(tmp_path)
    original_source = source.read_bytes()
    assert verify_import(source, archive, alignment)['status'] == 'passed'
    with ZipFile(archive) as z:
        files = {name: z.read(name) for name in z.namelist()}
    manifest = yaml.safe_load(files['manifest.yaml'])
    recipe = manifest['song_import']
    assert recipe['fingerVibratoPolicy'] == NOTE_VIBRATO_POLICY
    if fault == 'missing':
        del recipe['fingerVibratoPolicy']
    elif fault == 'wrong':
        recipe['fingerVibratoPolicy'] = WRITTEN_BEAT_VIBRATO_POLICY
    elif fault == 'downgrade':
        recipe['preservationContract'] = 93
    elif fault == 'downgrade_without_policy':
        recipe['preservationContract'] = 93
        del recipe['fingerVibratoPolicy']
    elif fault == 'bool':
        recipe['preservationContract'] = True
    elif fault == 'future':
        recipe['preservationContract'] = 95
    elif fault == 'old_inventory':
        inventory_name = recipe['compatibilityFile']
        inventory = json.loads(files[inventory_name])
        inventory['version'] = 93
        files[inventory_name] = json.dumps(inventory).encode()
    else:
        chart_name = manifest['arrangements'][0]['file']
        chart = json.loads(files[chart_name])
        note = chart['notes'][0]
        if fault == 'invented_vb':
            note['vb'] = True
        else:
            note['vibrato_marks'] = [{'start': 0, 'end': note['sus'], 'intensity': 'slight'}]
        files[chart_name] = json.dumps(chart).encode()
    files['manifest.yaml'] = yaml.safe_dump(manifest).encode()
    changed = tmp_path / f'{fault}.feedpak'
    with ZipFile(changed, 'w') as z:
        for name, content in files.items():
            z.writestr(name, content)
    checked = verify_import(source, changed, alignment)
    assert checked['status'] == 'failed', (fault, checked)
    assert source.read_bytes() == original_source


@pytest.mark.parametrize('historical,contract', [(False, 94), (True, 93)])
def test_wrong_caller_policy_is_rejected_before_packaging(tmp_path, historical, contract):
    with pytest.raises(ImportFailure):
        package(tmp_path, contract=contract, historical=historical,
                recipe_extra={'fingerVibratoPolicy': 'invented'})


def test_null_policy_cannot_be_attached_to_historical_contract(tmp_path):
    with pytest.raises(ImportFailure):
        package(tmp_path, contract=93, historical=True, recipe_extra={'fingerVibratoPolicy': None})


@pytest.mark.parametrize('contract', [True, 94.0, -1, 95])
def test_builder_rejects_invalid_or_unknown_contract_before_writing(tmp_path, contract):
    with pytest.raises(ImportFailure, match='Invalid preservation contract'):
        package(tmp_path, contract=contract)


def test_source_size_limit_precedes_hashing(tmp_path, monkeypatch):
    from pathlib import Path
    from feedback_converter.song_import import verify_source
    source, archive, alignment = package(tmp_path)
    source_bytes = Path.read_bytes
    def guarded_read(path):
        assert path != source, 'oversized source was read before its size guard'
        return source_bytes(path)
    monkeypatch.setattr(verify_source, 'MAX_SOURCE', 1)
    monkeypatch.setattr(Path, 'read_bytes', guarded_read)
    checked = verify_import(source, archive, alignment)
    assert checked['status'] == 'failed'
    assert any('size limit' in error['message'] for error in checked['errors'])
