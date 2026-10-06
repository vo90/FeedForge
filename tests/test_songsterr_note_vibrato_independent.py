"""Independent controller ownership, historical receipts and archive corruption."""
from copy import deepcopy
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from test_song_import_score import beat, measure, raw_score
from test_song_import_builder import inputs
from feedback_converter.song_import.builder import build_feedpak
from feedback_converter.song_import.compatibility import inspect_songsterr
from feedback_converter.song_import.evidence import CONTRACT_VERSION
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.verification import verify_import
from feedback_converter.song_import.verify_source import read_source, songsterr
from feedback_converter.song_import.verify_timeline import expected

CURRENT = 'songsterr-note-vibrato-v1'
HISTORICAL = 'songsterr-written-beat-vibrato-v1'


def source(*, field='vibrato', explicit=False, different=False):
    late = beat(0 if different else 3, string=4, duration=(1, 4), tie=True,
                **({field: True} if explicit else {}))
    late['notes'].append({'string': 3, 'fret': 2, 'vibrato': True})
    late[field] = True
    return raw_score([measure(beat(3, string=4, duration=(1, 4)), late,
        {'duration': [1, 2], 'notes': [{'rest': True}]})])


def derived(document, current=True):
    return expected(songsterr(document, note_owned_vibrato=current), {'offset': 0, 'scale': 1})


def flattened(result):
    return [r['note'] for part in result['parts'] for r in part['notes']]


def atoms(document, current=True):
    return [a for p in songsterr(document, note_owned_vibrato=current).parts for b in p.bars for a in b]


@pytest.mark.parametrize('field', ['vibrato', 'wideVibrato'])
@pytest.mark.parametrize('version', [None, 5, 8])
@pytest.mark.parametrize('context', ['attack', 'tie', 'chord', 'rest', 'voices', 'repeat'])
def test_beat_summary_does_not_supply_note_controller(field, version, context):
    doc = source(field=field)
    bar = doc['parts'][0]['measures'][0]
    if version is not None:
        doc['parts'][0]['version'] = version
    late = bar['voices'][0]['beats'][1]
    if context == 'attack':
        late['notes'][0].pop('tie')
    elif context == 'chord':
        late['notes'].append({'string': 2, 'fret': 7})
    elif context == 'rest':
        late['notes'].append({'rest': True})
    elif context == 'voices':
        bar['voices'].append({'beats': [beat(9, string=1)]})
    elif context == 'repeat':
        bar.update(repeatStart=True, repeat=2)
    original = deepcopy(doc)
    current, historical = atoms(doc), atoms(doc, False)
    own = [a for a in current if a.string == 2 and a.q == 1][0]
    assert own.finger_vibrato == 'slight' and own.effects['vb'] is True
    for a in current:
        if a is not own:
            assert a.finger_vibrato is None and not a.beat_vibrato and not a.effects.get('vb')
    old = [a for a in historical if a.string == 1 and a.q == 1][0]
    assert old.beat_vibrato and old.effects['vb'] is True
    assert old.finger_vibrato == ('wide' if field == 'wideVibrato' else 'slight')
    result = derived(doc)
    for p in result['parts']:
        fact = [b for b in p['notation_beats'] if b['voice'] == '0' and b['location'].endswith('/beats/1')]
        assert fact and fact[0]['notation']['vibw' if field == 'wideVibrato' else 'vib'] is True
        for row in fact:
            assert all(not n.get('vib') and not n.get('vibw') for n in row['notes'] if n['str'] != 2)
    assert doc == original


@pytest.mark.parametrize('field', ['vibrato', 'wideVibrato'])
def test_explicit_continuation_controller_retains_its_late_onset(field):
    doc = source(field=field, explicit=True)
    current, old = derived(doc), derived(doc, False)
    notes = flattened(current)
    held = [n for n in notes if n['s'] == 1][0]
    assert held['vibrato_marks'] == [{'start': .5, 'end': 1.,
                                    'intensity': 'wide' if field == 'wideVibrato' else 'slight'}]
    assert flattened(current) == flattened(old)
    assert held['t'] == 0 and held['sus'] == 1 and held['f'] == 3


def test_modern_note_width_precedes_legacy_note_and_beat_flags():
    doc = source(field='wideVibrato', explicit=True)
    doc['parts'][0]['measures'][0]['voices'][0]['beats'][1]['notes'][0]['leftHandVibrato'] = 'slight'
    for current in (True, False):
        a = [a for a in atoms(doc, current) if a.string == 1 and a.q == 1][0]
        assert a.finger_vibrato == 'slight' and not a.wide_vibrato and not a.beat_vibrato


def test_plain_zero_fret_tie_is_not_misclassified_as_a_new_vibrato_controller():
    doc = source(different=True)
    result = derived(doc)
    notes = flattened(result)
    held = [n for n in notes if n['s'] == 1][0]
    assert held == {'t': 0., 'sus': 1., 's': 1, 'f': 3}
    receipt = result['plain_tie_identities'][0]
    assert receipt['authored'] == {'fret': 0} and receipt['used'] == {'fret': 3}
    assert receipt['rule'] == 'plain-tie-keeps-attack-target'
    with pytest.raises(ValueError, match='continuous prior note'):
        derived(doc, False)
    with pytest.raises(ValueError, match='continuous prior note'):
        derived(source(different=True, explicit=True))


@pytest.mark.parametrize('current', [True, False])
def test_read_source_forwards_explicit_archive_policy(tmp_path, current):
    doc = source()
    path = tmp_path/'raw.json'
    path.write_text(json.dumps(doc), encoding='utf-8')
    before = path.read_bytes()
    read = expected(read_source(path, note_owned_vibrato=current), {'offset': 0, 'scale': 1})
    assert flattened(read) == flattened(derived(doc, current))
    assert path.read_bytes() == before


@pytest.mark.parametrize('selection', [None, 0, 1, '', 'historical'])
def test_policy_switch_cannot_be_selected_by_truthiness(selection):
    with pytest.raises(ValueError, match='vibrato policy selection'):
        songsterr(source(), note_owned_vibrato=selection)


REFERENCE = json.loads((Path(__file__).parent/'fixtures/songsterr_beat_vibrato_bends_reference.json').read_text())


@pytest.mark.parametrize('case', REFERENCE['cases'], ids=lambda c: c['id'])
def test_historical_bend_receipt_remains_exact_and_new_receipt_has_no_inferred_controller(case):
    doc = deepcopy(case['source'])
    old, current = derived(doc, False), derived(doc)
    assert flattened(old)[0] == {k: v for k, v in case['note'].items() if k != 'source_ids'}
    old_receipt = old['finger_bends'][0]
    new_receipt = current['finger_bends'][0]
    assert old_receipt['terminalSlideOut']['beatVibrato']['policy'] == 'independent-written-instruction'
    assert 'beatVibrato' not in new_receipt['terminalSlideOut']
    assert new_receipt['status'] == old_receipt['status'] == 'resolved'
    assert flattened(current)[0]['bnv'] == flattened(old)[0]['bnv']
    if '-mixed-' in case['id']:
        width = doc['parts'][0]['measures'][0]['voices'][0]['beats'][1]['notes'][0]['leftHandVibrato']
        assert flattened(current)[0]['vibrato_marks'] == [{'start': .5, 'end': 1., 'intensity': width}]
    else:
        assert not flattened(current)[0].get('vb') and 'vibrato_marks' not in flattened(current)[0]
    assert doc == case['source']


def package(tmp_path, *, current=True, explicit=False, bend_case=False):
    doc = deepcopy(REFERENCE['cases'][0]['source']) if bend_case else source(explicit=explicit)
    path = tmp_path/'score.json'
    path.write_text(json.dumps(doc), encoding='utf-8')
    performance = render(parse(doc, vibrato_policy=CURRENT if current else HISTORICAL))
    report = inspect_songsterr(doc, note_owned_vibrato=current)
    if not current:
        report['version'] = 93
    performance['compatibilityReport'] = report
    _, audio, _, directory = inputs(tmp_path)
    alignment = {'status': 'validated', 'offset': .25, 'scale': 1}
    result = build_feedpak(performance, audio, alignment, directory, output_dir=tmp_path/'out',
        source_path=path, compatibility=report,
        recipe={'preservationContract': CONTRACT_VERSION if current else 93})
    archive = Path(result['stagingPath'])
    checked = verify_import(path, archive, alignment)
    assert checked['status'] == 'passed', checked
    with ZipFile(archive) as z:
        files = {name: z.read(name) for name in z.namelist()}
    manifest = yaml.safe_load(files['manifest.yaml'])
    assert files[manifest['song_import']['sourceFile']] == path.read_bytes()
    return path, alignment, files, manifest


def write_archive(tmp_path, files, name):
    archive = tmp_path/(name+'.feedpak')
    with ZipFile(archive, 'w') as z:
        for path, value in files.items():
            z.writestr(path, value)
    return archive


@pytest.mark.parametrize('current', [True, False])
@pytest.mark.parametrize('bend_case', [True, False])
def test_current_and_historical_archives_select_their_own_source_semantics(tmp_path, current, bend_case):
    path, alignment, files, manifest = package(tmp_path, current=current, bend_case=bend_case)
    info = manifest['song_import']
    assert info['preservationContract'] == (CONTRACT_VERSION if current else 93)
    assert info.get('fingerVibratoPolicy') == (CURRENT if current else None)
    chart = json.loads(files[manifest['arrangements'][0]['file']])
    held = next(n for n in chart['notes'] if n.get('sus'))
    assert bool(held.get('vb')) is not current
    assert bool(held.get('vibrato_marks')) is not current
    assert verify_import(path, write_archive(tmp_path, files, 'unmodified'), alignment)['status'] == 'passed'


@pytest.mark.parametrize('fault', ['invented-held-vb', 'removed-explicit-vb', 'removed-explicit-interval',
                                 'changed-intensity', 'changed-onset', 'missing-warning', 'invented-warning'])
def test_current_archive_rejects_controller_and_warning_corruption(tmp_path, fault):
    path, alignment, files, manifest = package(tmp_path)
    chart_path = manifest['arrangements'][0]['file']
    chart = json.loads(files[chart_path])
    held = next(n for n in chart['notes'] if n['s'] == 1)
    explicit = next(n for n in chart['notes'] if n['s'] == 2)
    if fault == 'invented-held-vb':
        held.update(vb=True, vibrato_marks=[{'start': .5, 'end': 1., 'intensity': 'slight'}])
    elif fault == 'removed-explicit-vb':
        explicit.pop('vb')
    elif fault == 'removed-explicit-interval':
        explicit.pop('vibrato_marks')
    elif fault == 'changed-intensity':
        explicit['vibrato_marks'][0]['intensity'] = 'wide'
    elif fault == 'changed-onset':
        explicit['vibrato_marks'][0]['start'] = .1
    elif fault in ('missing-warning', 'invented-warning'):
        report_path = manifest['song_import']['compatibilityFile']
        report = json.loads(files[report_path])
        if fault == 'missing-warning':
            report['findings'] = [r for r in report['findings'] if r['feature'] != 'beat.vibrato']
        else:
            row = deepcopy(next(r for r in report['findings'] if r['feature'] == 'beat.vibrato'))
            row['location'] = 'parts/0/measures/0/voices/0/beats/0/vibrato'
            report['findings'].append(row)
        report['findingCount'] = len(report['findings'])
        files[report_path] = json.dumps(report).encode()
    files[chart_path] = json.dumps(chart).encode()
    checked = verify_import(path, write_archive(tmp_path, files, fault), alignment)
    assert checked['status'] == 'failed', fault


@pytest.mark.parametrize('fault', ['removed-interval', 'new-rule', 'changed-source'])
def test_historical_bend_archive_still_rejects_corruption(tmp_path, fault):
    path, alignment, files, manifest = package(tmp_path, current=False, bend_case=True)
    if fault == 'removed-interval':
        chart_path = manifest['arrangements'][0]['file']
        chart = json.loads(files[chart_path])
        chart['notes'][0].pop('vibrato_marks')
        files[chart_path] = json.dumps(chart).encode()
    else:
        receipt_path = manifest['song_import']['fingerBendTimingFile']
        receipt = json.loads(files[receipt_path])
        control = receipt['gestures'][0]['terminalSlideOut']['beatVibrato']
        if fault == 'new-rule':
            control['policy'] = 'explicit-note-controls'
        else:
            control['segments'][0]['sourceId'] += ':invented'
        files[receipt_path] = json.dumps(receipt).encode()
    checked = verify_import(path, write_archive(tmp_path, files, fault), alignment)
    assert checked['status'] == 'failed', fault
