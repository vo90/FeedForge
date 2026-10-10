"""Independent score-clock reuse must preserve every verification outcome."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from feedback_converter.song_import import load_performance
from feedback_converter.song_import import verification, verify_high_frets, verify_timeline, verify_tremolo
from feedback_converter.song_import.builder import build_feedpak
from feedback_converter.song_import.compatibility import new_report
from test_song_import_builder import inputs
from test_song_import_score import beat, measure, raw_score, write_gp


def package(tmp_path, *, contract=115, high=True, expression=True, identity=False, gp=False, document=None):
    rows = [beat(26 if high else 3, duration=(1, 4)), beat(5, string=1, duration=(1, 4)),
            beat(7, string=2, duration=(1, 4)), beat(9, string=3, duration=(1, 4))]
    if expression:
        rows[1]['letRing'] = rows[2]['letRing'] = True
        rows[3]['notes'][0]['tremolo'] = [1, 16]
    if gp:
        source = write_gp(tmp_path)
    else:
        source = tmp_path / 'source.json'
        source.write_text(json.dumps(document or raw_score([measure(*rows)])), encoding='utf-8')
    performance = load_performance(source)
    _, audio, _, job = inputs(tmp_path)
    alignment = {'status': 'validated', 'offset': 0 if identity else .75, 'scale': 1 if identity else 1.1}
    built = build_feedpak(performance, audio, alignment, job, output_dir=tmp_path / 'out',
                         recipe={'preservationContract': contract}, source_path=source,
                         compatibility=performance.get('compatibilityReport') or new_report())
    return source, Path(built['stagingPath']), alignment


def watch_reconstructions(monkeypatch):
    original = verify_timeline.expected
    calls, references = [], []

    def counted(source, alignment, **kwargs):
        result = original(source, alignment, **kwargs)
        calls.append((dict(alignment), dict(kwargs)))
        if alignment == {'offset': 0, 'scale': 1}:
            references.append((result, deepcopy(result)))
        return result

    monkeypatch.setattr(verification, 'expected', counted)
    monkeypatch.setattr(verify_timeline, 'expected', counted)
    monkeypatch.setattr(verify_tremolo, 'expected', counted)
    return calls, references


@pytest.mark.parametrize('fault', [None, 'receipt-fret', 'receipt-time', 'receipt-hash',
                                 'receipt-missing', 'ring-span', 'tremolo-period'])
def test_reuse_matches_reconstructed_reference_reports_and_rejects_corruption(tmp_path, monkeypatch, fault):
    source, archive, alignment = package(tmp_path)
    with ZipFile(archive) as z:
        files = {name: z.read(name) for name in z.namelist()}
    manifest = yaml.safe_load(files['manifest.yaml'])
    receipt_name = manifest['song_import']['highFretOmissionsFile']
    receipt = json.loads(files[receipt_name])
    chart_name = manifest['arrangements'][0]['file']
    chart = json.loads(files[chart_name])
    if fault == 'receipt-fret':
        receipt['notes'][0]['fret'] = 27
    elif fault == 'receipt-time':
        receipt['notes'][0]['scoreDuration'] += .01
    elif fault == 'receipt-hash':
        receipt['sourceSha256'] = '0' * 64
    elif fault == 'receipt-missing':
        manifest['song_import'].pop('highFretOmissionsFile')
    elif fault == 'ring-span':
        next(n for n in chart['notes'] if 'let_ring_sus' in n)['let_ring_sus'] += .01
    elif fault == 'tremolo-period':
        next(n for n in chart['notes'] if 'tremolo_marks' in n)['tremolo_marks'][0]['period'] += .01
    files[receipt_name] = json.dumps(receipt).encode()
    files[chart_name] = json.dumps(chart).encode()
    files['manifest.yaml'] = yaml.safe_dump(manifest).encode()
    mutated = tmp_path / 'checked.feedpak'
    with ZipFile(mutated, 'w') as z:
        for name, data in files.items():
            z.writestr(name, data)
    before = source.read_bytes(), mutated.read_bytes(), deepcopy(alignment)
    calls, references = watch_reconstructions(monkeypatch)
    optimized = verification.verify_import(source, mutated, alignment)
    assert optimized['status'] == ('passed' if fault is None else 'failed'), optimized
    assert len(references) == 1
    assert all(actual == snapshot for actual, snapshot in references), 'raw clock was projected or decorated'
    optimized_counts = optimized['counts'].copy()

    original_verify = verify_high_frets.verify

    def reconstruct_again(*args, reference=None, **kwargs):
        # Control uses the previous omission path: reconstruct the raw score
        # independently even though expression checks already own a reference.
        return original_verify(*args, **kwargs)

    monkeypatch.setattr(verify_high_frets, 'verify', reconstruct_again)
    calls.clear()
    references.clear()
    sequential = verification.verify_import(source, mutated, alignment)
    assert len(references) == 2
    assert optimized == sequential
    assert sequential['counts'] == optimized_counts
    assert before == (source.read_bytes(), mutated.read_bytes(), alignment)
    assert all(actual == snapshot for actual, snapshot in references)


@pytest.mark.parametrize('contract,identity,high,expression,raw_count,flags', [
    (94, False, True, False, 1, (False, False)),
    (95, False, True, False, 1, (True, False)),
    (96, False, True, False, 1, (True, True)),
    (109, False, True, True, 1, (True, True)),
    (110, False, True, True, 1, (True, True)),
    (111, False, True, True, 1, (True, True)),
    (115, False, True, True, 1, (True, True)),
    (115, False, False, True, 1, (True, True)),
    (115, False, False, False, 1, (True, True)),
    (115, True, False, False, 0, (True, True)),
    (115, True, True, False, 1, (True, True)),
    (115, True, True, True, 2, (True, True)),
    (109, False, False, False, 0, (True, True)),
])
def test_only_eligible_clocks_share_and_historical_tie_flags_survive(
        tmp_path, monkeypatch, contract, identity, high, expression, raw_count, flags):
    source, archive, alignment = package(tmp_path, contract=contract, identity=identity,
                                         high=high, expression=expression)
    calls, references = watch_reconstructions(monkeypatch)
    # With identity alignment the initial mapped expectation is also identity;
    # exclude that mandatory reconstruction from the optional raw-clock count.
    report = verification.verify_import(source, archive, alignment)
    assert report['status'] == 'passed', report
    raw_calls = [(mapping, options) for mapping, options in calls if mapping == {'offset': 0, 'scale': 1}]
    assert len(raw_calls) == raw_count
    assert all((options.get('pending_hopo_ties', True), options.get('pending_slide_ties', True)) == flags
               for _, options in raw_calls)
    assert all(actual == snapshot for actual, snapshot in references)


def test_non_songsterr_does_not_reconstruct_an_unused_expression_clock(tmp_path, monkeypatch):
    source, archive, alignment = package(tmp_path, gp=True)
    calls, references = watch_reconstructions(monkeypatch)
    report = verification.verify_import(source, archive, alignment)
    assert report['status'] == 'passed', report
    assert report['format'] == 'gpif'
    assert len(calls) == 1 and not references


@pytest.mark.parametrize('kind', ['hopo', 'slide', 'trill'])
def test_tied_and_expanded_high_fret_events_keep_reference_identity(tmp_path, monkeypatch, kind):
    if kind == 'hopo':
        from test_songsterr_pending_hopo_ties import source as document
        doc = document(origin=24, stored=0, destination=26)
    elif kind == 'slide':
        from test_songsterr_pending_slide_ties import source as document
        doc = document()
        doc['parts'][0]['measures'][0]['voices'][0]['beats'][0]['notes'][0]['fret'] = 26
    else:
        from test_songsterr_trills import trill
        doc = raw_score([measure(trill(7, 9), beat(26, duration=(1, 4)))])
    source, archive, alignment = package(tmp_path, document=doc)
    calls, references = watch_reconstructions(monkeypatch)
    report = verification.verify_import(source, archive, alignment)
    assert report['status'] == 'passed', report
    assert report['omissions']['omittedNotes'] >= 1
    assert len(references) == 1
    assert all(actual == snapshot for actual, snapshot in references)
    original_verify = verify_high_frets.verify
    monkeypatch.setattr(verify_high_frets, 'verify',
                        lambda *a, reference=None, **k: original_verify(*a, **k))
    assert verification.verify_import(source, archive, alignment) == report


def test_historical_voice_contract_keeps_original_omission_reconstruction(tmp_path, monkeypatch):
    source, archive, alignment = package(tmp_path, contract=24, expression=False)
    original = verify_timeline._expected
    raw_calls = []

    def historical(source, mapping, **kwargs):
        if mapping == {'offset': 0, 'scale': 1}:
            raw_calls.append(kwargs)
        return original(source, mapping, **kwargs)

    monkeypatch.setattr(verify_timeline, '_expected', historical)
    report = verification.verify_import(source, archive, alignment)
    assert report['status'] == 'passed', report
    assert raw_calls == [{'pending_hopo_ties': False, 'pending_slide_ties': False}]


def test_collapsed_opening_shares_complete_unshifted_reference(tmp_path, monkeypatch):
    from test_songsterr_collapsed_opening import document, prepare, META
    from feedback_converter.song_import.preparation import finalize

    document = document()
    document['parts'][0]['measures'][1] = measure(
        beat(26, duration=(1, 4)), beat(5, string=1, duration=(1, 4)),
        beat(7, string=2, duration=(1, 4)), beat(9, duration=(1, 4), tremolo=[1, 16]))
    document['parts'][0]['measures'][1]['voices'][0]['beats'][1]['letRing'] = True
    source, performance, audio, job, alignment = prepare(tmp_path, raw=document)
    audio, alignment = finalize(performance, audio, alignment, job)
    built = build_feedpak(performance, audio, alignment, job, output_dir=tmp_path / 'out',
                         source_path=source, compatibility=performance['compatibilityReport'],
                         recipe={'preservationContract': 115, 'scoreHash': hashlib.sha256(source.read_bytes()).hexdigest(),
                                 'audioHash': audio['hash'],
                                 'audioSource': audio['source'], 'preparation': alignment['preparation'],
                                 'alignment': {'provenance': alignment['provenance']}})
    calls, references = watch_reconstructions(monkeypatch)
    report = verification.verify_import(source, Path(built['stagingPath']), alignment, META)
    assert report['status'] == 'passed', report
    assert report['omissions']['omittedOpeningNotes'] == 1
    assert report['omissions']['omittedNotes'] == 1
    assert len(references) == 1
    actual, snapshot = references[0]
    assert actual == snapshot
    assert 'collapsed_opening_notes' not in actual and 'pre_opening_parts' not in actual
    assert any(item['note']['f'] == 26 for part in actual['parts'] for item in part['notes'])
    assert actual['parts'][0]['notes'][0]['note']['t'] == 0
