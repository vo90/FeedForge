from copy import deepcopy
from fractions import Fraction
import json
from pathlib import Path
from zipfile import ZipFile
import pytest
from test_song_import_score import beat, measure, raw_score
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected


def source():
    chord = {'duration': [1, 16], 'type': 16,
             'arpeggio': {'direction': 'up', 'duration': 86, 'shift': 100},
             'notes': [{'fret': 5, 'string': s} for s in range(4)]}
    grace = {**beat(fret=7, duration=(1, 32)), 'type': 32, 'graceNote': 'beforeBeat'}
    return raw_score([measure(beat(duration=(1, 4)), chord, grace, beat(fret=9, duration=(1, 4)))])


def test_consumed_note_preserved_as_evidence_without_attack_chord_or_notation():
    doc = source(); before = deepcopy(doc)
    p = render(parse(doc)); v = expected(songsterr(doc), {'offset': 0, 'scale': 1})
    assert p['consumedStrumEvidence'] == v['consumed_strums']
    row, = p['consumedStrumEvidence']
    assert row['sourceId'] == 'songsterr:0:0:0:1:3'
    assert row['authored'] == {'writtenQuarters': '1/4', 'soundingQuarters': '1/8', 'attackOffsetQuarters': '43/320'}
    assert row['attack'] > row['end']
    assert 'notation' not in p['tracks'][0]
    assert len(p['tracks'][0]['notes']) == len(v['parts'][0]['notes']) == 6
    assert len(p['strumEvidence'][0]['notes']) == 3
    assert doc == before


@pytest.mark.parametrize('delta,omitted', [(Fraction(-1, 10**9), False), (Fraction(0), True), (Fraction(1, 10**9), True)])
def test_exact_boundary_no_tolerance_drops_a_positive_sustain(delta, omitted):
    score = parse(source()); note = next(n for n in score.tracks[0].bars[0] if n.source_id.endswith(':1:3'))
    note.attack_offset = note.duration + delta
    result = render(score)
    assert bool(result.get('consumedStrumEvidence')) is omitted


@pytest.mark.parametrize('mode', ['no_strum', 'not_shortened', 'staccato', 'slide_in', 'hopo_link', 'tie_extension'])
def test_policy_does_not_hide_other_invalid_timing_or_tied_notes(mode):
    score = parse(source()); notes = score.tracks[0].bars[0]
    target = next(n for n in notes if n.source_id.endswith(':1:3'))
    if mode == 'no_strum': score.source_document['document']['parts'][0]['measures'][0]['voices'][0]['beats'][1].pop('arpeggio')
    elif mode == 'not_shortened':
        written = score.tracks[0].written_bars[0][0].beats[1]; written.written_duration = target.duration
    elif mode == 'staccato': target.staccato = True
    elif mode == 'slide_in': target.slide_in = 'up'
    elif mode == 'hopo_link':
        first = notes[0]; first.string = target.string; target.hopo = True
    elif mode == 'tie_extension':
        continued = deepcopy(target); continued.tie = True; continued.position += target.duration
        continued.duration = Fraction(1, 4); continued.source_id = 'songsterr:0:0:0:9:0'; notes.append(continued)
        result = render(score)
        assert not result.get('consumedStrumEvidence')
        assert any(target.source_id in n.get('source_ids', []) for n in result['tracks'][0]['notes'])
        return
    with pytest.raises(ValueError): render(score)


@pytest.mark.parametrize('fault', [None, 'receipt_missing', 'receipt_extra', 'wrong_offset', 'report_missing', 'contract'])
def test_finished_package_accounts_for_omission_and_rejects_tampering(tmp_path, fault):
    from test_song_import_builder import inputs
    from feedback_converter.song_import import load_performance
    from feedback_converter.song_import.builder import build_feedpak
    from feedback_converter.song_import.verification import verify_import
    import yaml
    _, audio, _, job = inputs(tmp_path)
    path = tmp_path / 'source.json'; path.write_text(json.dumps(source()), encoding='utf-8')
    p = load_performance(path); alignment = {'status': 'validated', 'offset': 0, 'scale': 1}
    built = build_feedpak(p, audio, alignment, job, output_dir=tmp_path/'out', source_path=path,
                         compatibility=p['compatibilityReport'], recipe={'preservationContract': 52})
    archive = Path(built['stagingPath'])
    if fault:
        with ZipFile(archive) as z: files = {n:z.read(n) for n in z.namelist()}
        receipt = json.loads(files['import/consumed-strums.json'])
        if fault == 'receipt_missing': receipt['omissions'] = []
        if fault == 'receipt_extra': receipt['omissions'] *= 2
        if fault == 'wrong_offset': receipt['omissions'][0]['authored']['attackOffsetQuarters'] = '1/8'
        files['import/consumed-strums.json'] = json.dumps(receipt).encode()
        if fault == 'report_missing':
            report = json.loads(files['import/compatibility.json']); report['findings'] = []; report['findingCount'] = 0; report['status'] = 'compatible'
            files['import/compatibility.json'] = json.dumps(report).encode()
        if fault == 'contract':
            manifest = yaml.safe_load(files['manifest.yaml']); manifest['song_import']['preservationContract'] = 51
            files['manifest.yaml'] = yaml.safe_dump(manifest).encode()
        archive = tmp_path/'changed.feedpak'
        with ZipFile(archive, 'w') as z:
            for name, data in files.items(): z.writestr(name, data)
    checked = verify_import(path, archive, alignment)
    assert checked['status'] == ('failed' if fault else 'passed'), checked
