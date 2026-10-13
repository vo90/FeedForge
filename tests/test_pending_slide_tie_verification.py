"""Source-bound archive checks for a pending slide across hidden plain ties."""
from copy import deepcopy
import json

import pytest

from test_pending_hopo_tie_verification import package, read_archive, write_archive
from test_songsterr_pending_slide_ties import source
from feedback_converter.song_import.verification import verify_import

SIDECAR = 'import/plain-tie-identity.json'


def test_archive_verifier_is_independent_of_producer(tmp_path, monkeypatch):
    from feedback_converter.song_import import pending_slide_ties
    path, archive, alignment = package(tmp_path, source(vibrato=True))
    def forbidden(*args, **kwargs):
        raise AssertionError('Producer helper reused by verifier')
    monkeypatch.setattr(pending_slide_ties, 'admitted', forbidden)
    monkeypatch.setattr(pending_slide_ties, 'record', forbidden)
    result = verify_import(path, archive, alignment)
    assert result['status'] == 'passed', result
    assert 'pending_slide_tie_identity' in result['scope']


def test_separate_voice_arrangements_keep_original_slide_lineage(tmp_path):
    from test_song_import_score import beat
    raw = source()
    raw['parts'][0]['measures'][0]['voices'].append({'beats':[beat(8,string=2,duration=(1,8))]})
    path, archive, alignment = package(tmp_path, raw)
    files, manifest = read_archive(archive)
    assert len(manifest['arrangements']) == 2
    row, = json.loads(files[SIDECAR])['continuations']
    assert row['trackId'].startswith('voice-') and row['voice'] == 0
    assert row['markerSourceId'] == 'songsterr:0:0:0:0:0'
    result = verify_import(path, archive, alignment)
    assert result['status'] == 'passed', result


@pytest.mark.parametrize('contract', [95,96])
def test_archive_local_sync_passes_slide_contract_to_independent_reader(tmp_path, monkeypatch, contract):
    from feedback_converter.song_import import local_sync
    path, archive, alignment = package(tmp_path, source(stored=2))
    files, manifest = read_archive(archive)
    manifest['song_import']['preservationContract'] = contract
    receipt = {'version':local_sync.REPAIR, 'status':'supported'}
    alignment = {**alignment, 'openingRepair':receipt}
    manifest['song_import']['openingRepairFile'] = 'import/opening-repair.json'
    files['import/opening-repair.json'] = json.dumps(receipt).encode()
    seen = []
    def verify_route(s, mapping, retained, recording, check, *, pending_hopo_ties, pending_slide_ties,
                     preservation_contract=0, rest_bridge_ties=None):
        seen.append(pending_slide_ties)
        assert preservation_contract == contract
        local_sync.independent_tracks(s, pending_hopo_ties=pending_hopo_ties, pending_slide_ties=pending_slide_ties,
            rest_bridge_ties=preservation_contract >= 122 if rest_bridge_ties is None else rest_bridge_ties)
    monkeypatch.setattr(local_sync, 'verify_repair', verify_route)
    result = verify_import(path, write_archive(tmp_path, files, manifest), alignment)
    assert result['status'] == 'passed', result
    assert seen == [contract >= 96]


@pytest.mark.parametrize('fault', ['hash', 'markerSourceId', 'originSourceId', 'sourceId', 'markerLocation',
    'markerOccurrence', 'occurrence', 'voice', 'string', 'markerStart', 'markerEnd', 'attack', 'start', 'end',
    'authored_fret', 'used_fret', 'slide', 'tie_type', 'rule', 'duplicate', 'extra', 'missing', 'reference',
    'policy', 'contract', 'inventory', 'fret', 'sustain', 'slide_target', 'slide_missing', 'fake_attack', 'notation'])
def test_tampered_receipt_and_chart_are_rejected(tmp_path, fault):
    path, archive, alignment = package(tmp_path, source(vibrato=True))
    assert verify_import(path, archive, alignment)['status'] == 'passed'
    files, manifest = read_archive(archive)
    receipt = json.loads(files[SIDECAR]);row = receipt['continuations'][0]
    if fault == 'hash': receipt['sourceSha256'] = '0' * 64
    if fault in ('markerSourceId','originSourceId','sourceId','markerLocation','rule'): row[fault] = 'invented'
    if fault in ('markerOccurrence','occurrence','voice','string'): row[fault] += 1
    if fault in ('markerStart','markerEnd','attack','start','end'): row[fault] += .02
    if fault == 'authored_fret': row['authored']['fret'] = 9
    if fault == 'used_fret': row['used']['fret'] = 9
    if fault == 'slide': row['used']['pendingSlide'] = 'legato'
    if fault == 'tie_type': row['authored']['tie'] = 1
    if fault == 'duplicate': receipt['continuations'] *= 2
    if fault == 'extra': receipt['unused'] = True
    files[SIDECAR] = json.dumps(receipt).encode()
    if fault == 'missing': files.pop(SIDECAR)
    recipe = manifest['song_import']
    if fault == 'reference': recipe.pop('plainTieIdentityFile')
    if fault == 'policy': recipe['plainTieIdentityPolicy'] = 'invented'
    if fault == 'contract': recipe['preservationContract'] = 95
    if fault == 'inventory':
        report = json.loads(files[recipe['compatibilityFile']]);report['version'] = 95
        files[recipe['compatibilityFile']] = json.dumps(report).encode()
    chart_path = manifest['arrangements'][0]['file']
    chart = json.loads(files[chart_path]);held = chart['notes'][0]
    if fault == 'fret': held['f'] = 0
    if fault == 'sustain': held['sus'] -= .125
    if fault == 'slide_target': held['sl'] = 9
    if fault == 'slide_missing': held.pop('sl')
    if fault == 'fake_attack': chart['notes'].insert(1, {'t':.125, 's':3, 'f':0, 'sus':.25})
    if fault in ('fret','sustain','slide_target','slide_missing','fake_attack'):
        from feedback_converter.chart_guidance import finalize
        finalize(chart, regenerate=True)
    files[chart_path] = json.dumps(chart).encode()
    if fault == 'notation':
        key = manifest['arrangements'][0]['notation'];notation = json.loads(files[key])
        notation['measures'][0]['staves']['staff']['voices'][0]['beats'][1]['notes'][0]['fret'] = 0
        files[key] = json.dumps(notation).encode()
    result = verify_import(path, write_archive(tmp_path, files, manifest), alignment)
    assert result['status'] == ('unsupported' if fault == 'contract' else 'failed'), result


@pytest.mark.parametrize('contract', [90,93,94,95])
def test_equal_fret_old_archive_still_verifies(tmp_path, contract):
    path, archive, alignment = package(tmp_path, source(stored=2))
    files, manifest = read_archive(archive)
    manifest['song_import']['preservationContract'] = contract
    if contract < 94: manifest['song_import'].pop('fingerVibratoPolicy', None)
    result = verify_import(path, write_archive(tmp_path, files, manifest), alignment)
    assert result['status'] == 'passed', result
    assert 'pending_slide_tie_identity' not in result['scope']


@pytest.mark.parametrize('field', ['plainTieIdentityFile', 'plainTieIdentityPolicy'])
@pytest.mark.parametrize('value', [None,False,'',0])
def test_empty_extra_policy_does_not_opt_in_source(tmp_path, field, value):
    path, archive, alignment = package(tmp_path, source(stored=2))
    files, manifest = read_archive(archive)
    manifest['song_import'][field] = value
    result = verify_import(path, write_archive(tmp_path, files, manifest), alignment)
    assert result['status'] == 'failed', result


@pytest.mark.parametrize('field,value', [('preservationContract',95), ('preservationContract',True),
                                       ('version',95), ('version',True)])
def test_builder_requires_current_literal_contract_and_inventory(tmp_path, field, value):
    from feedback_converter.song_import import load_performance
    from feedback_converter.song_import.builder import build_feedpak
    from test_song_import_builder import inputs
    path = tmp_path/'score.json';path.write_text(json.dumps(source()))
    performance = load_performance(path)
    recipe = {'preservationContract':96};compatibility=deepcopy(performance['compatibilityReport'])
    (recipe if field == 'preservationContract' else compatibility)[field] = value
    _, audio, _, job = inputs(tmp_path)
    with pytest.raises(ValueError):
        build_feedpak(performance, audio, {'status':'validated','offset':0,'scale':1}, job,
                      output_dir=tmp_path/'out', source_path=path, recipe=recipe, compatibility=compatibility)
