import hashlib
import json

import pytest

from feedback_converter.song_import.artwork import POLICY_VERSION, _title_identity
from feedback_converter.song_import.album_match import _context_versions, _matches
from test_song_import_artwork import Network, recording, release, uid


@pytest.mark.parametrize('marker', ['DJ mix', 'DJ-mix', 'DJ\u2010mix', 'DJ\u2011mix',
                                    'DJ\u2013mix', 'dj MIX', 'DJ mixed', 'DJmix'])
def test_dj_mix_cannot_create_false_artist_ambiguity(tmp_path, marker):
    network = Network()
    mixed = recording(9, disambiguation=f'part of “Dance Collection” {marker}')
    mixed['artist-credit'][0]['artist']['id'] = uid(99)
    network.records.append(mixed)
    result = network.resolve(tmp_path)
    assert result['status'] == 'matched'
    assert result['provenance']['recordingIds'] == [uid(2)]
    assert result['album'] == 'Original Album'


@pytest.mark.parametrize('annotation', ['original stereo mix', 'original mix', 'mixed by Engineer'])
def test_ordinary_mix_is_not_excluded(tmp_path, annotation):
    network = Network()
    network.records[0]['disambiguation'] = annotation
    assert network.resolve(tmp_path)['status'] == 'matched'


def test_genuine_artist_ambiguity_stays_ambiguous(tmp_path):
    network = Network()
    other = recording(9)
    other['artist-credit'][0]['artist']['id'] = uid(99)
    network.records.append(other)
    assert network.resolve(tmp_path)['reason'] == 'artist_ambiguous'
    assert not any('coverartarchive' in c['url'] for c in network.calls)


def test_dj_mix_only_does_not_supply_ordinary_album_cover(tmp_path):
    network = Network()
    network.records[0]['disambiguation'] = 'part of a DJ-mix'
    assert network.resolve(tmp_path)['status'] != 'matched'
    assert not any('coverartarchive' in c['url'] for c in network.calls)


def test_pinned_dj_mix_does_not_substitute_an_ordinary_recording(tmp_path):
    network = Network()
    network.records.append(recording(9, disambiguation='DJ mix'))
    assert network.resolve(tmp_path, musicbrainzRecordingId=uid(9))['status'] != 'matched'


@pytest.mark.parametrize('title', ['Example Song (DJ-mix)', 'Example Song [DJ\u2010mix]',
                                 'Example Song - DJ mix'])
def test_explicit_source_version_is_distinct_and_can_use_labelled_original_cover(tmp_path, title):
    network = Network()
    assert _title_identity(title)[1] == {'djmix'}
    assert not _matches(recording(), {'artist': 'Example Artist'}, title, {'djmix'}, set())
    result = network.resolve(tmp_path, title=title)
    assert result['reason'] == 'original_song_album_cover'
    assert 'album' not in result and 'year' not in result
    assert result['provenance']['matchingScope'] == 'original-song-artwork'


def test_audio_context_marks_dj_mix_only_when_artist_and_song_are_corroborated(tmp_path):
    network = Network()
    result = network.resolve(tmp_path, audioTitle='Example Artist - Example Song (DJ\u2010mix)')
    assert result['reason'] == 'original_song_album_cover'
    assert network.resolve(tmp_path / 'unrelated', audioTitle='Someone Else - Different Song DJ mix')['reason'] == 'album_cover'


def test_literal_song_title_and_artist_names_are_not_version_labels():
    assert _title_identity('DJ Mix')[1] == set()
    assert _context_versions('Example Song', 'DJ Mix - Example Song', ['DJ Mix'])[0] == set()


def test_old_ambiguous_cache_does_not_hide_fix(tmp_path):
    network = Network()
    cache = tmp_path / 'cache'
    cache.mkdir()
    values = {'artist': 'Example Artist', 'title': 'Example Song', 'album': '', 'audioTitle': ''}
    key = hashlib.sha256(json.dumps([6, values], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    (cache / f'{key}.json').write_text(json.dumps({'version': 6, 'createdAt': network.clock(),
        'ttl': 86400, 'result': {'status': 'ambiguous', 'reason': 'artist_ambiguous'}}))
    assert POLICY_VERSION > 6
    result = network.resolve(tmp_path / 'out', cache=cache)
    assert result['status'] == 'matched'
    count = len(network.calls)
    assert network.resolve(tmp_path / 'again', cache=cache)['status'] == 'matched'
    assert len(network.calls) == count
