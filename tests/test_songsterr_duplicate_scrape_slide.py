from copy import deepcopy
import json
from pathlib import Path
from fractions import Fraction
from zipfile import ZipFile

import pytest
from test_song_import_score import beat, measure, raw_score
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected


@pytest.mark.parametrize('direction,slide', [('up', 'upwards'), ('down', 'downwards')])
@pytest.mark.parametrize('fret', [None, 0, 10, 34])
@pytest.mark.parametrize('staccato', [False, True])
def test_identical_scrape_slide_is_one_visual_gesture(direction, slide, fret, staccato):
    b = beat(dead=True, pickScrape=direction, slide=slide, staccato=staccato)
    if fret is None:
        b['notes'][0].pop('fret')
    else:
        b['notes'][0]['fret'] = fret
    doc = raw_score([measure(b)])
    original = deepcopy(doc)
    result = render(parse(doc))['tracks'][0]['notes'][0]
    verified = expected(songsterr(doc), {'offset': 0, 'scale': 1})['parts'][0]['notes'][0]['note']
    assert result['pick_scrape_marks'] == verified['pick_scrape_marks']
    for note in (result, verified):
        assert note['mt'] is True
        assert 'slide_out' not in note and 'slide_out_marks' not in note
    assert doc == original


@pytest.mark.parametrize('different', ['direction', 'interval'])
def test_conflicting_or_different_intervals_are_not_silently_removed(different):
    b = beat(dead=True, pickScrape='down', slide='upwards' if different == 'direction' else 'downwards', duration=(1, 2))
    beats = [b, beat(dead=True, tie=True, duration=(1, 2))] if different == 'interval' else [b]
    doc = raw_score([measure(*beats)])
    result = render(parse(doc))['tracks'][0]['notes'][0]
    verified = expected(songsterr(doc), {'offset': 0, 'scale': 1})['parts'][0]['notes'][0]['note']
    for note in (result, verified):
        assert note['pick_scrape_marks'] and note['slide_out_marks']


@pytest.mark.parametrize('directions', [('down', 'down'), ('up', 'up', 'up'), ('down', 'down', 'up', 'up')])
@pytest.mark.parametrize('repeated', [False, True])
def test_tied_partitions_preserve_exact_scrape_coverage_and_attacks(directions, repeated):
    beats = [beat(dead=True, pickScrape=d, slide='downwards' if d == 'down' else 'upwards',
                  tie=i > 0, duration=(1, len(directions))) for i, d in enumerate(directions)]
    doc = raw_score([measure(*beats, **({'repeatStart': True, 'repeat': 2} if repeated else {}))])
    original = deepcopy(doc)
    produced = render(parse(doc))['tracks'][0]['notes']
    checked = [n['note'] for n in expected(songsterr(doc), {'offset': 0, 'scale': 1})['parts'][0]['notes']]
    assert len(produced) == len(checked) == (2 if repeated else 1)
    plain = deepcopy(doc)
    for b in plain['parts'][0]['measures'][0]['voices'][0]['beats']:
        b['notes'][0].pop('slide')
    assert produced == render(parse(plain))['tracks'][0]['notes']
    for a, b in zip(produced, checked):
        assert a['pick_scrape_marks'] == b['pick_scrape_marks']
        assert a['sus'] == b['sus'] == 2
        assert 'slide_out_marks' not in a and 'slide_out_marks' not in b
    assert doc == original


def test_tempo_and_piecewise_mapping_preserve_deduplicated_tied_gesture():
    from feedback_converter.song_import.builder import _retime_note
    doc = raw_score([measure(beat(dead=True, pickScrape='down', slide='downwards', duration=(1, 2)),
                             beat(dead=True, tie=True, pickScrape='down', slide='downwards', duration=(1, 2)))])
    doc['parts'][0]['automations']['tempo'].append({'measure': 0, 'position': 1920, 'bpm': 60, 'type': 4})
    alignment = {'mapping': 'piecewise-linear', 'anchors': [{'score': 0, 'audio': 1},
                  {'score': 1, 'audio': 2}, {'score': 3, 'audio': 6}]}
    note, = render(parse(doc))['tracks'][0]['notes']
    mapped = _retime_note(note, alignment, 10)
    checked = expected(songsterr(doc), alignment)['parts'][0]['notes'][0]['note']
    assert mapped['pick_scrape_marks'] == checked['pick_scrape_marks'] == [{'direction': 'down', 'start': 0, 'end': 5}]
    assert 'slide_out_marks' not in mapped and 'slide_out_marks' not in checked


def test_repeated_attacks_are_not_merged_into_one_scrape():
    doc = raw_score([measure(*[beat(dead=True, pickScrape='up', slide='upwards', duration=(1, 2)) for _ in range(2)])])
    produced = render(parse(doc))['tracks'][0]['notes']
    checked = expected(songsterr(doc), {'offset': 0, 'scale': 1})['parts'][0]['notes']
    assert len(produced) == len(checked) == 2
    assert [n['t'] for n in produced] == [0, 1]
    assert all('slide_out_marks' not in n for n in produced)


@pytest.mark.parametrize('delta', [Fraction(1, 10**12), Fraction(1, 10)])
def test_exact_coverage_comparisons_never_bridge_gaps_or_hide_extent_changes(delta):
    from feedback_converter.song_import.timeline import _directional_coverage
    from feedback_converter.song_import.verify_timeline import _direction_boundaries
    whole = [('down', Fraction(0), Fraction(2))]
    partition = [('down', Fraction(0), Fraction(1)), ('down', Fraction(1), Fraction(2))]
    for compare in (_directional_coverage, _direction_boundaries):
        assert compare(whole) == compare(partition)
        for different in (
            [('down', Fraction(0), Fraction(1)), ('down', Fraction(1) + delta, Fraction(2))],
            [('down', Fraction(0), Fraction(2) + delta)],
            [('down', Fraction(0), Fraction(1)), ('up', Fraction(1), Fraction(2))],
        ):
            assert compare(whole) != compare(different)
        assert compare([('down', Fraction(0), Fraction(1)), ('down', Fraction(1) - delta, Fraction(2))]) is None


@pytest.mark.parametrize('tied', [False, True])
def test_package_and_independent_verification_keep_scrape_and_reject_duplicate_reintroduced(tmp_path, tied):
    from test_song_import_builder import inputs
    from feedback_converter.song_import import load_performance
    from feedback_converter.song_import.builder import build_feedpak
    from feedback_converter.song_import.verification import verify_import
    from feedback_converter.feedpak_validator import validate_feedpak
    _, audio, _, job = inputs(tmp_path)
    b = beat(dead=True, pickScrape='down', slide='downwards', fret=10, duration=(1, 2) if tied else (1, 1))
    b['notes'].append({'string': 1, 'dead': True, 'pickScrape': 'down'})
    source = tmp_path / 'source.json'
    beats = [b]
    if tied:
        continuation = deepcopy(b)
        for n in continuation['notes']:
            n['tie'] = True
        beats.append(continuation)
    source.write_text(json.dumps(raw_score([measure(*beats)])))
    p = load_performance(source)
    alignment = {'status': 'validated', 'offset': 0, 'scale': 1}
    built = build_feedpak(p, audio, alignment, job, output_dir=tmp_path/'out', source_path=source,
                         compatibility=p['compatibilityReport'], recipe={'preservationContract': 117})
    archive = Path(built['stagingPath'])
    assert validate_feedpak(archive).ok
    assert verify_import(source, archive, alignment)['status'] == 'passed'
    with ZipFile(archive) as z:
        files = {name: z.read(name) for name in z.namelist()}
    chart_name = next(name for name in files if name.startswith('arrangements/'))
    chart = json.loads(files[chart_name])
    note = chart['chords'][0]['notes'][0]
    note['slide_out_marks'] = deepcopy(note['pick_scrape_marks'])
    note['slide_out'] = 'down'
    files[chart_name] = json.dumps(chart).encode()
    changed = tmp_path / 'tampered.feedpak'
    with ZipFile(changed, 'w') as z:
        for name, data in files.items(): z.writestr(name, data)
    assert not validate_feedpak(changed).ok
    assert verify_import(source, changed, alignment)['status'] == 'failed'
