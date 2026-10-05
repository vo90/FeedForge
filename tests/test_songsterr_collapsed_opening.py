from copy import deepcopy
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from feedback_converter.song_import import load_performance
from feedback_converter.song_import.audio import ImportFailure
from feedback_converter.song_import.synchronization import align_from_songsterr, map_source_time
from feedback_converter.song_import.builder import build_feedpak
from feedback_converter.song_import.preparation import finalize
from feedback_converter.song_import.verification import verify_import
from test_song_import_score import raw_score, measure, beat
from test_song_import_builder import inputs

META = {'songId': '12', 'revisionId': '34', 'approval': 'approved'}
VIDEO = 'abcdefghijk'


def document(kind='single'):
    first = beat(0)
    if kind == 'rest':
        first = {'duration': [1, 4], 'rest': True, 'notes': [{'rest': True}]}
    if kind == 'chord':
        first['notes'].append({'string': 1, 'fret': 3})
    raw = raw_score([measure(first), measure(beat(2)), measure(beat(5))])
    raw.update(songId='12', revisionId='34')
    return raw


def prepare(tmp_path, kind='single', points=(.53, .53, 2.62, 4.76), hybrid=False, raw=None):
    path = tmp_path/'source.json'
    path.write_text(json.dumps(raw or document(kind)), encoding='utf-8')
    p = load_performance(path, metadata=META, composition_context=hybrid)
    _, audio, _, job = inputs(tmp_path)
    audio['source'] = {'kind': 'youtube', 'videoId': VIDEO}
    sync = {'version': 1, 'source': 'songsterr-video-points', **META, 'videoId': VIDEO,
            'status': 'done', 'feature': None, 'points': list(points)}
    a = align_from_songsterr(p, audio, sync, META)
    return path, p, audio, job, a


@pytest.mark.parametrize('kind,count', [('single', 1), ('chord', 2), ('rest', 0)])
@pytest.mark.parametrize('hybrid', [False, True])
def test_complete_feedpak_skips_only_opening_and_preserves_source(tmp_path, kind, count, hybrid):
    source, p, audio, job, a = prepare(tmp_path, kind, hybrid=hybrid)
    original = deepcopy(p)
    assert a['collapsedOpening']['noteCount'] == count
    assert map_source_time(a, 0) == map_source_time(a, 1) == .53
    assert map_source_time(a, 3) == 1.575
    audio, a = finalize(p, audio, a, job)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    recipe = {'preservationContract': 87, 'source': 'songsterr', 'scoreHash': digest,
              'audioHash': audio['hash'], 'audioSource': audio['source'],
              'alignment': {'provenance': deepcopy(a['provenance'])}, 'preparation': a['preparation']}
    options = None
    if hybrid:
        from feedback_converter.song_import.hybrid_lead import choose_main, normalize_options
        options = normalize_options({'enabled': True})
        options.update(mainTrackId=choose_main(p, options, digest), sourceSha256=digest)
        recipe['hybridLead'] = options
    result = build_feedpak(p, audio, a, job, output_dir=tmp_path/'out', recipe=recipe,
                          source_path=source, compatibility=p['compatibilityReport'],
                          hybrid_lead={'enabled': True, 'mainTrackId': options['mainTrackId'], 'options': options} if hybrid else None)
    assert any('opening' in w for w in result['warnings'])
    report = verify_import(source, Path(result['stagingPath']), a, META, hybrid_options=options)
    assert report['status'] == 'passed', report
    assert report['omissions']['omittedOpeningNotes'] == count
    with ZipFile(result['stagingPath']) as z:
        manifest = yaml.safe_load(z.read('manifest.yaml'))
        assert z.read(manifest['song_import']['sourceFile']) == source.read_bytes()
        receipt = json.loads(z.read('import/collapsed-opening.json'))
        assert len(receipt['notes']) == count
        for arr in manifest['arrangements']:
            chart = json.loads(z.read(arr['file']))
            assert [n['f'] for n in chart['notes']] == [2, 5]
            assert chart['notes'][0]['t'] == pytest.approx(2, abs=1/22050)
        notation = json.loads(z.read(manifest['arrangements'][0]['notation']))
        assert [m['source_measure'] for m in notation['measures']] == [2, 3]
    assert {k: v for k, v in p.items() if k != "hybridBaseSelection"} == original


@pytest.mark.parametrize('points', [(0, 0, 0, 0), (0, 1, 1, 2), (0, 0, 2, 1), (1, 0, 2, 3)])
def test_invalid_maps_still_rejected(tmp_path, points):
    with pytest.raises(ImportFailure) as exc:
        prepare(tmp_path, points=points)
    assert exc.value.diagnostics['sourceSyncReason'] == 'non_increasing_points'


def test_notes_crossing_skipped_bar_are_not_cut(tmp_path):
    _, p, audio, _, a = prepare(tmp_path)
    p['tracks'][0]['notes'][0]['sus'] = 2.5
    with pytest.raises(ImportFailure) as exc:
        align_from_songsterr(p, audio, a['sourceTiming'], META)
    assert exc.value.diagnostics['sourceSyncReason'] == 'collapsed_opening_crossing'


@pytest.mark.parametrize('fault', ['missing_receipt', 'missing_note', 'fret', 'source_id', 'boundary', 'note_count', 'shift_later_note', 'reinsert_note', 'old_contract'])
def test_final_verifier_rejects_changed_omissions_or_music(tmp_path, fault):
    source, p, audio, job, a = prepare(tmp_path)
    audio, a = finalize(p, audio, a, job)
    recipe = {'preservationContract': 87, 'scoreHash': hashlib.sha256(source.read_bytes()).hexdigest(),
              'audioSource': audio['source'], 'alignment': {'provenance': a['provenance']}, 'preparation': a['preparation']}
    built = build_feedpak(p, audio, a, job, output_dir=tmp_path/'out', source_path=source,
                          compatibility=p['compatibilityReport'], recipe=recipe)
    with ZipFile(built['stagingPath']) as z:
        files = {name: z.read(name) for name in z.namelist()}
    manifest = yaml.safe_load(files['manifest.yaml'])
    receipt = json.loads(files['import/collapsed-opening.json'])
    if fault == 'missing_note': receipt['notes'].clear()
    if fault == 'fret': receipt['notes'][0]['fret'] = 10
    if fault == 'source_id': receipt['notes'][0]['sourceIds'] = ['invented']
    if fault == 'boundary':
        a['collapsedOpening']['scoreEnd'] += .1
        manifest['song_import']['collapsedOpening'] = deepcopy(a['collapsedOpening'])
        receipt['scoreEnd'] += .1
    if fault == 'note_count': receipt['noteCount'] += 1
    if fault == 'old_contract': manifest['song_import']['preservationContract'] = 86
    if fault in ('shift_later_note', 'reinsert_note'):
        name = manifest['arrangements'][0]['file']; chart = json.loads(files[name])
        if fault == 'shift_later_note': chart['notes'][0]['t'] += .1
        else: chart['notes'].insert(0, {**chart['notes'][0], 'f': 0, 'sus': 0})
        files[name] = json.dumps(chart).encode()
    files['import/collapsed-opening.json'] = json.dumps(receipt).encode()
    if fault == 'missing_receipt': files.pop('import/collapsed-opening.json')
    files['manifest.yaml'] = yaml.safe_dump(manifest).encode()
    archive = tmp_path/'changed.feedpak'
    with ZipFile(archive, 'w') as z:
        for name, data in files.items(): z.writestr(name, data)
    report = verify_import(source, archive, a, META)
    assert report['status'] == 'failed', report


def test_without_policy_negative_opening_brush_is_not_omitted():
    from feedback_converter.song_import.collapsed_opening import omitted
    assert not omitted({'offset': 2, 'scale': 1}, -.05)


def test_matches_native_forward_clock_and_opening_jump(tmp_path):
    fixture = json.loads((Path(__file__).parent/'fixtures/songsterr_collapsed_opening_reference.json').read_bytes())
    assert fixture['reference'][1]['sha256'] == '8b9267cd39f7f3b0511bade44de01cf3fe7c8025d5a7d534f8a6de448c940e17'
    for case in fixture['cases']:
        root = tmp_path/case['id']; root.mkdir()
        raw = document(); raw['parts'] = [case['input']]
        source, p, audio, job, a = prepare(root, points=case['points'], raw=raw)
        assert a['collapsedOpening']['scoreEnd'] == pytest.approx(case['inverseAtOpening'])
        for sample in case['samples']:
            assert map_source_time(a, sample['score']) == pytest.approx(sample['audio'], abs=1e-6)


@pytest.mark.parametrize('fault', ['entire_track', 'linked_boundary', 'strum_crossing'])
def test_unsafe_projection_is_rejected(tmp_path, fault):
    _, p, audio, _, a = prepare(tmp_path)
    track = p['tracks'][0]
    if fault == 'entire_track': track['notes'] = track['notes'][:1]
    if fault == 'linked_boundary': track['notes'][0]['ln'] = 1
    if fault == 'strum_crossing': p['strumEvidence'] = [{'notes': [{'t': 1.95}, {'t': 2.01}]}]
    with pytest.raises(ImportFailure) as exc:
        align_from_songsterr(p, audio, a['sourceTiming'], META)
    assert exc.value.diagnostics['sourceSyncReason'] == 'collapsed_opening_crossing'


@pytest.mark.parametrize('case', ['two_bars', 'high_before', 'high_after', 'lyrics', 'donor'])
def test_projection_interactions_verify_independently(tmp_path, case):
    raw = document()
    points = [.53, .53, 2.62, 4.76]
    if case == 'two_bars': points = [.53, .53, .53, 4.76]
    if case.startswith('high_'):
        bar = 0 if case == 'high_before' else 1
        raw['parts'][0]['measures'][bar]['voices'][0]['beats'][0]['notes'][0]['fret'] = 26
    if case == 'lyrics':
        raw['tracks'].append({'id': 1, 'name': 'Vocals', 'isVocalTrack': True, 'instrumentId': 53})
        raw['parts'].append({'withLyrics': True, 'newLyrics': [{'line': 1, 'offset': 1, 'text': 'One two three'}],
                             'measures': [measure(beat(duration=(1, 1))) for _ in range(3)]})
    if case == 'donor':
        raw['tracks'].append({**raw['tracks'][0], 'id': 1, 'name': 'Rhythm'})
        raw['parts'].append(deepcopy(raw['parts'][0]))
        raw['parts'][0]['measures'][0] = measure({'duration': [1, 1], 'rest': True, 'notes': [{'rest': True}]})
    source, p, audio, job, a = prepare(tmp_path, points=points, hybrid=True, raw=raw)
    audio, a = finalize(p, audio, a, job)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    from feedback_converter.song_import.hybrid_lead import choose_main, normalize_options
    options = normalize_options({'enabled': True})
    options.update(mainTrackId=choose_main(p, options, digest), sourceSha256=digest)
    recipe = {'preservationContract': 87, 'source': 'songsterr', 'scoreHash': digest,
              'audioHash': audio['hash'], 'audioSource': audio['source'], 'hybridLead': options,
              'alignment': {'provenance': a['provenance']}, 'preparation': a['preparation']}
    built = build_feedpak(p, audio, a, job, output_dir=tmp_path/'out', recipe=recipe,
                          source_path=source, compatibility=p['compatibilityReport'],
                          hybrid_lead={'enabled': True, 'mainTrackId': options['mainTrackId'], 'options': options})
    report = verify_import(source, Path(built['stagingPath']), a, META, hybrid_options=options)
    assert report['status'] == 'passed', report
