"""Grace slots carry no syllables but still borrow real performed time."""
from copy import deepcopy
from fractions import Fraction
import json
from zipfile import ZipFile

import pytest
import yaml

from test_song_import_score import beat
from test_songsterr_lyrics import document, timeline, package
from feedback_converter.song_import import songsterr_lyrics as lyrics
from feedback_converter.song_import.verification import verify_import
from feedback_converter.song_import.verification import Check, _json
from feedback_converter.song_import.verify_source import read_source
from feedback_converter.song_import.verify_lyrics import verify as verify_lyrics


def grace_document(kind='beforeBeat', bar=1):
    raw = document('one two three four five six seven eight', bars=2)
    grace = beat(duration=(1, 32)); grace['graceNote'] = kind
    raw['parts'][1]['measures'][bar]['voices'][0]['beats'].insert(0, grace)
    return raw


def test_cross_bar_grace_preserves_syllables_and_borrowing():
    raw = grace_document(); original = deepcopy(raw)
    result = timeline(raw)
    assert raw == original and result['policy'] == lyrics.POLICY
    assert result['status'] == 'ready'
    assert [e['text'] for e in result['events']] == 'one two three four five six seven eight'.split()
    assert [e['time'] for e in result['events']] == [0, .5, 1, 1.5, 2, 2.5, 3, 3.5]
    assert result['events'][3]['end'] == 1.9375
    assert result['events'][4]['end'] == 2.5
    old = lyrics.written(raw, [Fraction(4)] * 2, policy=lyrics.LEGACY_POLICY)
    assert old['status'] == 'unsupported' and 'boundary' in old['reason']


@pytest.mark.parametrize('kind,bar', [('onBeat', 1), ('beforeBeat', 0), ('onBeat', 0)])
def test_onbeat_and_initial_grace_keep_their_delay(kind, bar):
    result = timeline(grace_document(kind, bar))
    assert result['status'] == 'ready' and len(result['events']) == 8
    assert result['events'][4 * bar]['time'] == 2 * bar + .0625


@pytest.mark.parametrize('continuation', ['tie', 'extension', 'rest'])
def test_grace_does_not_extend_a_syllable_across_borrowed_gap(continuation):
    raw = grace_document()
    principal = raw['parts'][1]['measures'][1]['voices'][0]['beats'][1]
    if continuation == 'tie': principal['notes'][0]['tie'] = True
    elif continuation == 'extension': raw['parts'][1]['newLyrics'][0]['text'] = 'one two three four _ six seven eight'
    else: principal.update(rest=True, notes=[])
    result = timeline(raw)
    assert result['status'] == 'ready'
    assert result['events'][3]['end'] == 1.9375
    assert result['events'][4]['time'] == 2.5


def test_cross_bar_grace_repeats_without_shift_or_duplicate_syllables():
    raw = grace_document()
    for part in raw['parts']:
        part['measures'][0]['repeatStart'] = True
        part['measures'][1]['repeat'] = 2
    result = timeline(raw)['events']
    assert len(result) == 16
    assert [e['text'] for e in result[:8]] == [e['text'] for e in result[8:]]
    assert all(b['time'] == a['time'] + 4 and b['end'] == a['end'] + 4
               for a, b in zip(result[:8], result[8:]))


@pytest.mark.parametrize('fault', ['unknown_grace', 'invalid_duration', 'ordinary_overflow'])
def test_invalid_vocal_timing_is_still_unsupported(fault):
    raw = grace_document()
    beats = raw['parts'][1]['measures'][1]['voices'][0]['beats']
    if fault == 'unknown_grace': beats[0]['graceNote'] = 'invented'
    elif fault == 'invalid_duration': beats[0]['duration'] = [0, 32]
    else: beats.append(beat(duration=(1, 4)))
    assert timeline(raw)['status'] == 'unsupported'


def rewrite(path, target, mutate):
    with ZipFile(path) as z: files = {n: z.read(n) for n in z.namelist()}
    manifest = yaml.safe_load(files['manifest.yaml'])
    proof = json.loads(files['import/lyrics.json'])
    mutate(files, manifest, proof)
    files['manifest.yaml'] = yaml.safe_dump(manifest).encode()
    files['import/lyrics.json'] = json.dumps(proof).encode()
    with ZipFile(target, 'w') as z:
        for name, content in files.items(): z.writestr(name, content)
    return target


@pytest.mark.parametrize('fault', [None, 'time', 'duration', 'policy', 'proof_policy', 'remove'])
def test_grace_lyrics_archive_and_corruption(tmp_path, fault):
    source, archive, alignment = package(tmp_path, grace_document())
    if fault:
        def mutate(files, manifest, proof):
            if fault == 'policy': manifest['song_import']['lyricsPolicy'] = 'unknown'
            elif fault == 'proof_policy': proof['policy'] = lyrics.LEGACY_POLICY
            elif fault == 'remove': manifest.pop('lyrics')
            else:
                rows = json.loads(files['lyrics.json'])
                rows[3]['t' if fault == 'time' else 'd'] += .0625
                files['lyrics.json'] = json.dumps(rows).encode()
        archive = rewrite(archive, tmp_path/'changed.feedpak', mutate)
    result = verify_import(source, archive, alignment)
    assert result['status'] == ('failed' if fault else 'passed'), result


@pytest.mark.parametrize('with_grace', [False, True])
def test_historical_lyric_policy_remains_verifiable(tmp_path, monkeypatch, with_grace):
    original = lyrics.written
    with monkeypatch.context() as patch:
        patch.setattr(lyrics, 'written', lambda doc, lengths: original(doc, lengths, policy=lyrics.LEGACY_POLICY))
        source, archive, alignment = package(tmp_path, grace_document() if with_grace else document())
    def historical(files, manifest, proof):
        manifest['song_import']['lyricsPolicy'] = proof['policy'] = lyrics.LEGACY_POLICY
    archive = rewrite(archive, tmp_path/'historical.feedpak', historical)
    result = verify_import(source, archive, alignment)
    assert result['status'] == 'passed', result
    with ZipFile(archive) as z:
        assert json.loads(z.read('import/lyrics.json'))['status'] == ('unsupported' if with_grace else 'imported')


def test_current_contract_cannot_downgrade_lyric_policy(tmp_path):
    source, archive, alignment = package(tmp_path)
    with ZipFile(archive) as z:
        manifest = yaml.safe_load(z.read('manifest.yaml'))
        recipe = manifest['song_import']
        recipe.update(preservationContract=109, lyricsPolicy=lyrics.LEGACY_POLICY)
        check = Check()
        verify_lyrics(z, recipe, manifest, source, read_source(source), alignment, manifest['duration'], check, _json)
        assert any(e['code'] == 'lyrics_policy' for e in check.errors)
