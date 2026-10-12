"""Bounded public-player lyric text, receipts and historical rejection rules."""
from fractions import Fraction
import json
from zipfile import ZipFile

import pytest
import yaml

from test_songsterr_lyrics import document, timeline, package
from test_songsterr_lyrics_grace import rewrite, grace_document
from feedback_converter.song_import import songsterr_lyrics as lyrics
from feedback_converter.song_import.verification import verify_import, Check, _json
from feedback_converter.song_import.verify_source import read_source
from feedback_converter.song_import.verify_lyrics import verify, text_limit_evidence


@pytest.mark.parametrize('text,wanted,reason', [
    ('', '', None), ('One  two\tthree', 'One  two\tthree', None),
    ('a' * 20000, 'a' * 20000, None),
    ('a' * 20001, 'a' * 20000, 'utf16_limit'),
    ('a' * 19999 + '\U0001f600', 'a' * 19999, 'utf16_limit'),
    ('a' * 19998 + '\U0001f600Z', 'a' * 19998 + '\U0001f600', 'utf16_limit'),
    ('a' * 19999 + '\ud83d\ude00', 'a' * 19999, 'utf16_limit'),
    ('a' * 19999 + '\r\n', 'a' * 19999, 'utf16_limit'),
    *[(('word' + sep) * 499 + 'tail', ('word' + sep) * 499 + 'tail', None)
      for sep in ('\r', '\n', '\r\n')],
    *[(('word' + sep) * 500 + 'tail', ('word' + sep) * 499 + 'word', 'line_limit')
      for sep in ('\r', '\n', '\r\n')],
], ids=lambda value: None if value is None else str(len(value)))
def test_prefix_boundaries(text, wanted, reason):
    actual, receipt = lyrics.bounded_text(text)
    assert actual == wanted
    independent = text_limit_evidence(text, 0)
    if reason is None:
        assert receipt is independent is None
    else:
        assert receipt['reason'] == reason
        assert {**receipt, 'rowIndex': 0} == independent


def test_whitespace_slots_and_instruments_unchanged_by_unused_tail():
    short = document('one  two three\n')
    long = document('one  two three' + '\n' * 600)
    assert timeline(short)['events'] == timeline(long)['events']
    result = timeline(long)
    assert result['sourceTextLimit']['retainedLineBreaks'] == 499
    assert result['unassignedSyllables'] == 0
    assert result['warnings'] == [lyrics.PREFIX_WARNING]


@pytest.mark.parametrize('policy', [lyrics.LEGACY_POLICY, lyrics.GRACE_POLICY])
def test_historical_policies_keep_whole_stream_rejection(policy):
    result = lyrics.written(document('one two' + '\n' * 500), [Fraction(4)] * 3, policy=policy)
    assert result['status'] == 'unsupported'
    assert 'text limit' in result['reason'] and 'sourceTextLimit' not in result


def test_v2_grace_policy_stays_supported():
    result = lyrics.written(grace_document(), [Fraction(4)] * 2, policy=lyrics.GRACE_POLICY)
    assert result['status'] == 'ready'


def test_limit_records_selected_row_without_borrowing_other_rows():
    raw = document()
    raw['parts'][1]['newLyrics'] = [{'text': 'wrong', 'offset': 1} for _ in range(5)] + [
        {'text': 'chosen words' + '\n' * 500, 'offset': 2}]
    result = timeline(raw)
    assert result['sourceTextLimit']['rowIndex'] == 5
    assert [(e['text'], e['time']) for e in result['events']] == [('chosen', 2), ('words\n', 2.5)]


def test_large_unused_tail_does_not_allocate_a_large_token_stream():
    stream = lyrics.tokens('word ' * 200000)
    assert len(stream) <= 20001


@pytest.mark.parametrize('field', [None, 'policy', 'reason', 'rowIndex', 'originalUtf16Units',
    'retainedUtf16Units', 'originalLineBreaks', 'retainedLineBreaks', 'remove'])
def test_archive_checks_source_limit_receipt(tmp_path, field):
    source, archive, alignment = package(tmp_path, document('one two' + '\n' * 500))
    if field:
        def mutate(files, manifest, proof):
            if field == 'remove': proof.pop('sourceTextLimit')
            else: proof['sourceTextLimit'][field] = 'corrupted'
        archive = rewrite(archive, tmp_path / 'mutated.feedpak', mutate)
    result = verify_import(source, archive, alignment)
    assert result['status'] == ('failed' if field else 'passed'), result
    if field:
        assert any(e['code'] == 'lyrics_text_limit' for e in result['errors'])


@pytest.mark.parametrize('contract,policy,passes', [
    (108, lyrics.LEGACY_POLICY, True), (109, lyrics.GRACE_POLICY, True),
    (116, lyrics.GRACE_POLICY, True), (117, lyrics.POLICY, True),
    (117, lyrics.GRACE_POLICY, False), (116, lyrics.POLICY, False),
])
def test_policy_contract_and_historical_archives(tmp_path, monkeypatch, contract, policy, passes):
    original = lyrics.written
    with monkeypatch.context() as patch:
        patch.setattr(lyrics, 'written', lambda doc, lengths: original(doc, lengths, policy=policy))
        source, archive, alignment = package(tmp_path, document('one two' + '\n' * 500))
    def set_policy(files, manifest, proof):
        manifest['song_import'].update(lyricsPolicy=policy, preservationContract=contract)
        proof['policy'] = policy
    archive = rewrite(archive, tmp_path / 'historical.feedpak', set_policy)
    with ZipFile(archive) as z:
        manifest = yaml.safe_load(z.read('manifest.yaml'))
        check = Check()
        verify(z, manifest['song_import'], manifest, source, read_source(source), alignment,
               manifest['duration'], check, _json)
        assert (not check.errors) == passes, check.errors
