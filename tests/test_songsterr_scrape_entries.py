from copy import deepcopy
import json
from pathlib import Path
from zipfile import ZipFile
import pytest
from test_song_import_score import beat, measure, raw_score
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected


@pytest.mark.parametrize('direction', ['up', 'down'])
@pytest.mark.parametrize('slide', ['above', 'below'])
@pytest.mark.parametrize('fret', [None, 0, 12, 34])
def test_scrape_keeps_direction_timing_and_original_entry_without_pitched_path(direction, slide, fret):
    b = beat(dead=True, pickScrape=direction, slide=slide, ghost=True)
    if fret is None: b['notes'][0].pop('fret')
    else: b['notes'][0]['fret'] = fret
    doc = raw_score([measure(b)]); before = deepcopy(doc)
    p = render(parse(doc)); v = expected(songsterr(doc), {'offset': 0, 'scale': 1})
    n = p['tracks'][0]['notes'][0]; checked = v['parts'][0]['notes'][0]['note']
    assert n['pick_scrape_marks'] == checked['pick_scrape_marks'] == [{'direction': direction, 'start': 0., 'end': 2.}]
    assert n['mt'] and n['ghost'] and 'slide_in_marks' not in n and 'slide_in_marks' not in checked
    assert p['scrapeEntryEvidence'] == v['scrape_entries']
    assert p['scrapeEntryEvidence'][0]['authored'] == {'slide': slide}
    assert doc == before


def test_repeated_mixed_chord_preserves_normal_pitched_slide_and_only_accounts_for_scrape():
    b = beat(dead=True, pickScrape='up', slide='above', duration=(1, 1))
    b['notes'].append({'fret': 7, 'string': 1, 'slide': 'below'})
    doc = raw_score([measure(b, repeatStart=True, repeat=2)])
    p = render(parse(doc)); v = expected(songsterr(doc), {'offset': 0, 'scale': 1})
    assert [r['occurrence'] for r in p['scrapeEntryEvidence']] == [1, 2]
    assert p['scrapeEntryEvidence'] == v['scrape_entries']
    for chord in p['tracks'][0]['chords']:
        assert next(n for n in chord['notes'] if n['f'] == 7)['slide_in_marks'] == [{'direction': 'up', 'time': 0.}]


@pytest.mark.parametrize('staccato', [False, True])
def test_tied_scrape_inherits_scrape_and_keeps_incoming_mark_only_in_evidence(staccato):
    doc = raw_score([measure(beat(dead=True, pickScrape='down', staccato=staccato, duration=(1, 2)),
                             beat(dead=True, tie=True, slide='below', duration=(1, 2)))])
    p = render(parse(doc)); v = expected(songsterr(doc), {'offset': 0, 'scale': 1})
    n, = p['tracks'][0]['notes']
    assert n['sus'] == (1. if staccato else 2.)
    assert 'slide_in_marks' not in n
    assert p['scrapeEntryEvidence'] == v['scrape_entries']
    assert p['scrapeEntryEvidence'][0]['start'] == 1.


def test_unpitched_semantic_guard_still_rejects_pitched_slide():
    from feedback_converter.feedpak_semantics import validate_arrangement_semantics
    chart = {'notes': [{'t': 0, 's': 0, 'f': 127, 'mt': True, 'sus': 1,
                        'pick_scrape_marks': [{'direction': 'down', 'start': 0, 'end': 1}],
                        'slide_in_marks': [{'direction': 'up', 'time': 0}]}]}
    errors = []
    validate_arrangement_semantics(chart, {}, 'test', errors.append)
    assert any('pitched gesture' in e for e in errors)


@pytest.mark.parametrize('fault', [None, 'missing', 'extra', 'direction', 'source', 'report', 'pitched_entry', 'contract'])
def test_finished_scrape_package_requires_faithful_receipt_and_chart(tmp_path, fault):
    from test_song_import_builder import inputs
    from feedback_converter.song_import import load_performance
    from feedback_converter.song_import.builder import build_feedpak
    from feedback_converter.song_import.verification import verify_import
    import yaml
    _, audio, _, job = inputs(tmp_path)
    doc = raw_score([measure(beat(dead=True, pickScrape='down', slide='below', ghost=True))])
    path = tmp_path/'source.json'; path.write_text(json.dumps(doc), encoding='utf-8')
    p = load_performance(path); alignment = {'status': 'validated', 'offset': 0, 'scale': 1}
    built = build_feedpak(p, audio, alignment, job, output_dir=tmp_path/'out', source_path=path,
                         compatibility=p['compatibilityReport'], recipe={'preservationContract': 53})
    archive = Path(built['stagingPath'])
    if fault:
        with ZipFile(archive) as z: files = {n:z.read(n) for n in z.namelist()}
        receipt = json.loads(files['import/scrape-entries.json'])
        if fault == 'missing': receipt['gestures'] = []
        if fault == 'extra': receipt['gestures'] *= 2
        if fault == 'direction': receipt['gestures'][0]['authored']['slide'] = 'above'
        if fault == 'source': receipt['sourceSha256'] = '0'*64
        files['import/scrape-entries.json'] = json.dumps(receipt).encode()
        if fault == 'report':
            r = json.loads(files['import/compatibility.json']); r['findings'] = [x for x in r['findings'] if x['feature'] != 'note.scrape_entry']; r['findingCount'] = len(r['findings'])
            files['import/compatibility.json'] = json.dumps(r).encode()
        if fault == 'pitched_entry':
            name = next(n for n in files if n.startswith('arrangements/') and n.endswith('.json') and 'notation' not in n)
            chart = json.loads(files[name]); chart['notes'][0]['slide_in_marks'] = [{'direction': 'up', 'time': 0}]
            files[name] = json.dumps(chart).encode()
        if fault == 'contract':
            manifest = yaml.safe_load(files['manifest.yaml']); manifest['song_import']['preservationContract'] = 52
            files['manifest.yaml'] = yaml.safe_dump(manifest).encode()
        archive = tmp_path/'changed.feedpak'
        with ZipFile(archive, 'w') as z:
            for name, data in files.items(): z.writestr(name, data)
    checked = verify_import(path, archive, alignment)
    assert checked['status'] == ('failed' if fault else 'passed'), checked
