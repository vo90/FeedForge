"""Only independently verified album labels may shorten artwork lookup titles."""
import copy
from urllib.parse import parse_qs, urlsplit

import pytest

from feedback_converter.song_import import artwork
from test_song_import_artwork import Network, recording, release, response, uid


def label_network():
    network = Network()
    def override(url):
        parts = urlsplit(url)
        if parts.path == '/ws/2/recording/':
            query = parse_qs(parts.query)['query'][0]
            if not query.startswith('recording:"Example Song" AND '):
                return response({'count': 0, 'recordings': []})
    network.override = override
    return network


@pytest.mark.parametrize('title', ['Example Song [Original Album]', 'Example Song (Original Album)',
                                  'Example Song [original-album]'])
def test_album_label_requires_album_and_track_proof_without_changing_input(tmp_path, title):
    network = label_network()
    metadata = {'artist': 'Example Artist', 'title': title, 'album': '', 'audioTitle': 'Example Song'}
    before = copy.deepcopy(metadata)
    result = artwork.resolve_album_art(metadata, tmp_path, transport=network, clock=network.clock,
                                       sleep=network.clock.sleep, now=network.clock)
    assert result['status'] == 'matched'
    assert result['album'] == 'Original Album'
    proof = result['provenance']
    assert proof['albumSelection'] == 'original-official-release'
    assert proof['trackEvidence']['basis'] == 'original-year-release-track'
    assert proof['albumLabelResolution']['sourceTitle'] == title
    assert proof['albumLabelResolution']['lookupTitle'] == 'Example Song'
    assert proof['albumLabelResolution']['releaseGroupId'] == uid(4)
    assert metadata == before
    queries = [parse_qs(urlsplit(c['url']).query)['query'][0] for c in network.calls
               if urlsplit(c['url']).path == '/ws/2/recording/']
    assert title in queries[0]
    assert len(network.calls) <= artwork.MAX_REQUESTS


def test_exact_bracketed_title_wins_without_inferred_album(tmp_path):
    network = Network()
    network.records = [recording(title='Example Song [Original Album]')]
    result = network.resolve(tmp_path, title=network.records[0]['title'])
    assert result['status'] == 'matched'
    assert 'albumLabelResolution' not in result['provenance']


@pytest.mark.parametrize('title', ['Example Song [Wrong Album]', 'Example Song (Part Two)',
                                  'Example Song [Unknown Version]', 'Example Song [Original Album] (Extra)',
                                  'Example Song [Original Album)', 'Example Song []'])
def test_unverified_subtitles_do_not_produce_artwork(tmp_path, title):
    network = label_network()
    result = network.resolve(tmp_path, title=title)
    assert result['status'] != 'matched' and 'path' not in result
    assert not any('coverartarchive.org' in c['url'] for c in network.calls)


@pytest.mark.parametrize('metadata', [{'album': 'Different Album'}, {'musicbrainzRecordingId': uid(2)}])
def test_conflicting_explicit_identity_does_not_get_label_fallback(tmp_path, metadata):
    network = label_network()
    assert network.resolve(tmp_path, title='Example Song [Original Album]', **metadata)['status'] != 'matched'
    assert not any('coverartarchive.org' in c['url'] for c in network.calls)


@pytest.mark.parametrize('damage', ['artist', 'version', 'membership', 'missing-media', 'group-title', 'group-artist'])
def test_base_song_and_album_must_be_independently_verified(tmp_path, damage):
    network = label_network()
    original = network.override
    if damage == 'artist':
        network.records = [recording(artist='Another Artist')]
    elif damage == 'version':
        network.records = [recording(disambiguation='live')]
    def override(url):
        path = urlsplit(url).path
        if path == '/ws/2/release/' + uid(3) and damage in ('membership', 'missing-media'):
            full = release()
            if damage == 'membership':
                full['media'] = [{'position': 1, 'track-count': 1,
                                  'tracks': [{'position': 1, 'recording': recording(90, title='Other Song')}]}]
            return response(full)
        if path == '/ws/2/release-group/' + uid(4) and damage in ('group-title', 'group-artist'):
            full = release(title='Different Album') if damage == 'group-title' else release(artist='Other Artist')
            return response({**full['release-group'], 'artist-credit': full['artist-credit']})
        return original(url)
    network.override = override
    result = network.resolve(tmp_path, title='Example Song [Original Album]')
    assert result['status'] != 'matched'
    assert not any('coverartarchive.org' in c['url'] for c in network.calls)


def test_album_label_does_not_authorize_later_bonus_track(tmp_path):
    network = label_network()
    later = release(date='2015')
    later['release-group']['first-release-date'] = '2001'
    network.releases[uid(2)] = [later]
    result = network.resolve(tmp_path, title='Example Song [Original Album]')
    assert result['reason'] == 'album_track_unconfirmed'


def test_same_album_name_is_ambiguous_even_with_different_years(tmp_path):
    network = label_network()
    network.releases[uid(2)].append(release(10, 11, date='2010'))
    assert network.resolve(tmp_path, title='Example Song [Original Album]')['reason'] == 'album_ambiguous'


def test_audio_performance_context_is_not_discarded(tmp_path):
    network = label_network()
    result = network.resolve(tmp_path, title='Example Song [Original Album]',
                             audioTitle='Example Artist - Example Song Live')
    assert result['status'] != 'matched'
    assert not any('coverartarchive.org' in c['url'] for c in network.calls)


@pytest.mark.parametrize('status', [429, 500])
def test_transport_failure_is_not_a_semantic_title_miss(tmp_path, status):
    network = label_network()
    network.override = lambda url: response(status=status)
    assert network.resolve(tmp_path, title='Example Song [Original Album]')['status'] != 'matched'
    assert all('[Original Album]' in parse_qs(urlsplit(c['url']).query)['query'][0] for c in network.calls)


@pytest.mark.parametrize('limit', ['requests', 'seconds'])
def test_fallback_uses_original_request_and_time_budget(tmp_path, monkeypatch, limit):
    network = label_network()
    if limit == 'requests':
        monkeypatch.setattr(artwork, 'MAX_REQUESTS', 4)
    else:
        monkeypatch.setattr(artwork, 'MAX_SECONDS', 2.5)
    result = network.resolve(tmp_path, title='Example Song [Original Album]')
    assert result['reason'] == 'lookup_budget_exceeded'
    assert len(network.calls) <= 4


def test_old_negative_cache_is_invalidated_and_new_proof_is_cached(tmp_path, monkeypatch):
    network = label_network()
    metadata = {'title': 'Example Song [Original Album]'}
    with monkeypatch.context() as patch:
        patch.setattr(artwork, 'POLICY_VERSION', 8)
        original = network.override
        network.override = lambda url: response({'count': 0, 'recordings': []}) if urlsplit(url).path == '/ws/2/recording/' else original(url)
        assert network.resolve(tmp_path / 'old', cache=tmp_path / 'cache', **metadata)['status'] != 'matched'
    network.override = original
    result = network.resolve(tmp_path / 'new', cache=tmp_path / 'cache', **metadata)
    assert result['status'] == 'matched'
    network.calls.clear()
    cached = network.resolve(tmp_path / 'cached', cache=tmp_path / 'cache', **metadata)
    assert cached['provenance'] == result['provenance'] and not network.calls
