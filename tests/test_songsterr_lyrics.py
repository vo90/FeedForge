"""Synthetic lyric semantics, recording clocks and final archive compatibility."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from test_song_import_score import beat, measure, raw_score
from test_song_import_builder import inputs
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.score import load_performance
from feedback_converter.song_import.songsterr_lyrics import assign, primary_row, select
from feedback_converter.song_import.lyric_timeline import export, phrases
from feedback_converter.song_import.builder import build_feedpak
from feedback_converter.song_import.preparation import finalize
from feedback_converter.song_import.verification import verify_import

REFERENCE = json.loads((Path(__file__).parent / 'fixtures/songsterr_lyrics_reference.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('case', REFERENCE['cases'], ids=lambda c: c['id'])
def test_matches_pinned_public_player_syllable_slots(case):
    assert REFERENCE['referenceSha256'] == '50bb5cd2fefe0bc4ea02cc5af0cbec205571e4df56031f09d92975be6de87d85'
    rows, _ = assign(case['part'], case['text'], primary_row(case['part']).get('offset', 1))
    assert [[r['text'].strip() if not r['extension'] else '' for r in bar] for bar in rows] == case['expected']


def document(text='Sun-shine over the hill', bars=3):
    raw = raw_score([measure(beat()) for _ in range(bars)])
    raw['tracks'].append({'id': 1, 'name': 'Lead Vocals', 'isVocalTrack': True, 'instrumentId': 53})
    raw['parts'].append({'withLyrics': True, 'newLyrics': [{'line': 1, 'offset': 1, 'text': text}],
                         'measures': [measure(*(beat(duration=(1, 4)) for _ in range(4))) for _ in range(bars)]})
    return raw


def timeline(raw):
    original = deepcopy(raw)
    result = render(parse(raw))
    assert original == raw
    assert len(result['tracks']) == 1, 'vocals must not become a playable guitar arrangement'
    return result['lyricTimeline']


def test_syllable_slots_rest_tie_skip_and_word_join():
    raw = document('Sun-shine  over')
    beats = raw['parts'][1]['measures'][0]['voices'][0]['beats']
    beats[1]['notes'][0]['tie'] = True
    beats[2].update(rest=True, notes=[])
    value = timeline(raw)
    assert [(e['text'], e['time'], e['end']) for e in value['events']] == [
        ('Sun-', 0, 1), ('shine', 1.5, 2), ('over', 2.5, 3)]
    events, report = export(value, {'offset': 2, 'scale': 1}, 10)
    assert [e['w'] for e in events] == ['Sun-', 'shine', 'over+']
    assert events[0] == {'t': 2, 'd': 1, 'w': 'Sun-'}
    assert report['status'] == 'imported'


def test_starting_measure_and_swing():
    raw = document('one two')
    raw['parts'][1]['newLyrics'][0]['offset'] = 2
    raw['parts'][1]['measures'][1]['voices'][0]['beats'] = [beat(duration=(1, 8)) for _ in range(8)]
    raw['parts'][1]['measures'][1]['tripletFeel'] = '8th'
    rows = timeline(raw)['events']
    assert [r['time'] for r in rows] == pytest.approx([2, 2 + 1/3])
    assert [r['end'] - r['time'] for r in rows] == pytest.approx([1/3, 1/6])


def test_primary_row_after_five_alternate_rows():
    raw = document()
    raw['parts'][1]['newLyrics'] = [{'text': 'alternate', 'offset': 1} for _ in range(5)] + [
        {'text': 'primary words', 'offset': 2}]
    assert [(r['text'], r['time']) for r in timeline(raw)['events']] == [('primary', 2), ('words', 2.5)]


def test_underscore_extends_only_a_contiguous_syllable():
    raw = document('long _ word _')
    beats = raw['parts'][1]['measures'][0]['voices'][0]['beats']
    beats[3].update(rest=True, notes=[])
    assert [(r['text'], r['time'], r['end']) for r in timeline(raw)['events']] == [
        ('long', 0, 1), ('word', 1, 1.5)]


def test_repeats_tempo_changes_and_separate_endpoint_mapping():
    raw = document('one two three four', bars=2)
    for p in raw['parts']:
        p['measures'][0]['repeatStart'] = True
        p['measures'][1]['repeat'] = 2
    raw['parts'][0]['automations']['tempo'].append({'measure': 0, 'position': 1920, 'bpm': 60, 'type': 4})
    value = timeline(raw)
    assert [e['time'] for e in value['events']] == [0, .5, 1, 2, 7, 7.5, 8, 9]
    align = {'mapping': 'piecewise-linear', 'anchors': [{'score': 0, 'audio': 2},
             {'score': 1.5, 'audio': 3.5}, {'score': 14, 'audio': 28.5}]}
    events, _ = export(value, align, 30)
    assert events[2]['t'] == 3
    assert events[2]['d'] == 1.5, 'duration must map both endpoints across an anchor'


def test_one_main_stream_and_no_duplicate_backing_vocals():
    raw = document()
    raw['tracks'].append({'id': 2, 'name': 'Backing Vocals', 'isVocalTrack': True, 'instrumentId': 53})
    raw['parts'].append(deepcopy(raw['parts'][1]))
    raw['parts'][2].pop('withLyrics')
    value = timeline(raw)
    assert value['trackIndex'] == 1 and len(value['candidates']) == 2
    raw['parts'][1].pop('withLyrics')
    assert timeline(raw)['selection'] == 'first_vocal_track'
    raw['tracks'][1].update(isVocalTrack=False, name='First')
    raw['tracks'][2].update(isVocalTrack=False, name='Second')
    assert select(raw)[1]['status'] == 'unsupported'


@pytest.mark.parametrize('change', [lambda p: p.update(newLyrics='invalid'),
    lambda p: p['newLyrics'][0].update(text='x' * 20001),
    lambda p: p['newLyrics'][0].update(offset=999),
    lambda p: p['measures'][0]['voices'][0]['beats'][0].update(duration=[0, 4])])
def test_invalid_optional_lyrics_do_not_block_instrument_import(change):
    raw = document()
    change(raw['parts'][1])
    assert timeline(raw)['status'] == 'unsupported'


def test_missing_and_unavailable_lyrics():
    raw = document('')
    assert timeline(raw)['status'] == 'absent'
    raw['lyricsAcquisition'] = {'status': 'unavailable'}
    assert timeline(raw)['status'] == 'unavailable'


def test_legacy_sidecar_requires_exact_vocal_positions():
    raw = document('')
    raw['legacyLyrics'] = [{'beats': [{'duration': [1, 4], 'lyrics': [{'text': 'One'}]},
                                    {'duration': [1, 4], 'lyrics': [{'text': 'two'}]}]}]
    value = timeline(raw)
    assert value['sourceKind'] == 'legacy'
    assert [(r['time'], r['text']) for r in value['events']] == [(0, 'One'), (.5, 'two')]
    raw['legacyLyrics'][0]['beats'][0]['duration'] = [1, 8]
    assert timeline(raw)['status'] == 'unsupported'


def test_recording_clipping_and_no_extrapolated_lyrics():
    value = timeline(document())
    events, report = export(value, {'offset': -.25, 'scale': 1}, 1.5)
    assert events[0]['t'] == 0 and events[0]['d'] == .25
    assert events[-1]['t'] + events[-1]['d'] == 1.5
    assert report['clippedEvents'] == 2 and report['omittedEvents'] == 1
    events, report = export(value, {'mapping': 'piecewise-linear', 'anchors': [
        {'score': 0, 'audio': 0}, {'score': 1, 'audio': 1}]}, 8)
    assert events == [] and report['status'] == 'unsupported'


def test_bounded_phrases_and_joined_words():
    rows = [{'t': i*.5, 'd': .5, 'w': 'word'} for i in range(20)]
    grouped = phrases(rows)
    assert [i for i, e in enumerate(grouped) if e['w'].endswith('+')] == [7, 15, 19]
    rows[7]['w'] = 'part-'
    assert phrases(rows)[7]['w'] == 'part-'
    assert phrases(rows)[8]['w'].endswith('+')
    rows[9]['t'] = 10
    assert phrases(rows[:10])[8]['w'].endswith('+')


def test_source_newlines_and_multiword_syllable_are_translated():
    events, _ = export(timeline(document('one+two\nthree')), {'offset': 0, 'scale': 1}, 8)
    assert events == [{'t': 0, 'd': .5, 'w': 'one two+'}, {'t': .5, 'd': .5, 'w': 'three+'}]


def package(tmp_path, raw=None):
    raw = document() if raw is None else raw
    source = tmp_path / 'score.json'
    source.write_text(json.dumps(raw), encoding='utf-8')
    performance = load_performance(source)
    _, audio, _, job = inputs(tmp_path)
    alignment = {'status': 'validated', 'offset': 0, 'scale': 1}
    audio, alignment = finalize(performance, audio, alignment, job)
    result = build_feedpak(performance, audio, alignment, job, output_dir=tmp_path/'out',
                          source_path=source, compatibility=performance['compatibilityReport'],
                          recipe={'preservationContract': 78, 'preparation': alignment['preparation'],
                                  'scoreHash': hashlib.sha256(source.read_bytes()).hexdigest(), 'audioHash': audio['hash']})
    return source, Path(result['stagingPath']), alignment


@pytest.mark.parametrize('text', ['Sun-shine over the hill', '', 'x' * 20001], ids=['authored', 'absent', 'oversized'])
def test_final_feedpak_and_independent_clock_verification(tmp_path, text):
    source, archive, alignment = package(tmp_path, document(text))
    result = verify_import(source, archive, alignment)
    assert result['status'] == 'passed', result
    assert 'authored_lyrics_export' in result['scope']
    with ZipFile(archive) as z:
        manifest = yaml.safe_load(z.read('manifest.yaml'))
        if text and len(text) < 20000:
            events = json.loads(z.read(manifest['lyrics']))
            assert events[0] == {'t': 2, 'd': .5, 'w': 'Sun-'}
            assert all(set(e) == {'t', 'd', 'w'} for e in events)
            assert manifest['lyrics_source'] == 'authored'
        else:
            assert 'lyrics' not in manifest


@pytest.mark.parametrize('fault', ['time', 'duration', 'text', 'phrase', 'remove', 'selection', 'audio', 'contract'])
def test_verifier_rejects_corrupt_or_missing_lyrics(tmp_path, fault):
    source, archive, alignment = package(tmp_path)
    with ZipFile(archive) as z:
        contents = {n: z.read(n) for n in z.namelist()}
    events = json.loads(contents['lyrics.json'])
    if fault == 'time': events[0]['t'] += .1
    elif fault == 'duration': events[0]['d'] += .1
    elif fault == 'text': events[0]['w'] = 'different'
    elif fault == 'phrase': events[-1]['w'] = events[-1]['w'].rstrip('+')
    elif fault in ('selection', 'audio'):
        report = json.loads(contents['import/lyrics.json'])
        report['trackIndex' if fault == 'selection' else 'audioSha256'] = 999
        contents['import/lyrics.json'] = json.dumps(report).encode()
    else:
        manifest = yaml.safe_load(contents['manifest.yaml'])
        if fault == 'remove': manifest.pop('lyrics')
        else: manifest['song_import']['preservationContract'] = 77
        contents['manifest.yaml'] = yaml.safe_dump(manifest).encode()
    contents['lyrics.json'] = json.dumps(events).encode()
    altered = tmp_path / 'altered.feedpak'
    with ZipFile(altered, 'w') as z:
        for name, value in contents.items(): z.writestr(name, value)
    assert verify_import(source, altered, alignment)['status'] == 'failed'
