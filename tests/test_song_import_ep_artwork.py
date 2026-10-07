"""EP artwork survives later album bonus editions without relaxing identity."""
import copy
from urllib.parse import urlsplit

import pytest

from test_song_import_artwork import Network, credit, recording, release, response, uid
from feedback_converter.song_import import artwork


def ep_network():
    network = Network()
    original = release()
    bonus = release(5, date='2010')
    bonus['release-group']['first-release-date'] = '2001-04-03'
    ep = release(10, 11, 'Original EP', '2000')
    ep['release-group']['primary-type'] = 'EP'
    network.releases = {uid(2): [bonus, ep], uid(99): [original]}
    full = {**original, 'media': [{'position': 1, 'track-count': 1, 'tracks': [
        {'position': 1, 'recording': recording(99, title='Different Song')}]}]}
    network.override = lambda url: response(full) if urlsplit(url).path == '/ws/2/release/' + uid(3) else None
    network.cover['release'] = 'https://musicbrainz.org/release/' + uid(10)
    return network, full


def test_bonus_album_falls_back_to_independently_verified_ep(tmp_path):
    network, _ = ep_network()
    result = network.resolve(tmp_path)
    assert result['status'] == 'matched'
    assert (result['album'], result['year']) == ('Original EP', 2000)
    proof = result['provenance']
    assert proof['releaseType'] == 'EP'
    assert proof['trackEvidence']['releaseId'] == uid(10)
    assert proof['trackEvidence']['recordingId'] == uid(2)
    assert proof['albumCandidateChecks'] == [{
        'releaseGroupId': uid(4), 'album': 'Original Album',
        'reason': 'track-not-found-in-checked-original-editions',
        'checkedReleaseIds': [uid(3)], 'candidateEditionCount': 1,
        'editionLimitReached': False,
    }]


def test_verified_album_still_wins_over_earlier_ep(tmp_path):
    network, full = ep_network()
    full['media'][0]['tracks'][0]['recording'] = recording()
    network.cover['release'] = 'https://musicbrainz.org/release/' + uid(3)
    result = network.resolve(tmp_path)
    assert result['album'] == 'Original Album'
    assert 'albumCandidateChecks' not in result['provenance']


@pytest.mark.parametrize('damage', ['no-count', 'truncated', 'no-title', 'no-credit', 'no-recording',
                                   'wrong-position', 'wrong-media-count', 'wrong-artist', 'wrong-group', 'wrong-year'])
def test_incomplete_or_invalid_original_release_cannot_trigger_ep_fallback(tmp_path, damage):
    network, full = ep_network()
    medium = full['media'][0]
    record = medium['tracks'][0]['recording']
    if damage == 'no-count':
        medium.pop('track-count')
    elif damage == 'truncated':
        medium['track-count'] = 2
    elif damage == 'no-title':
        record.pop('title')
    elif damage == 'no-credit':
        record.pop('artist-credit')
    elif damage == 'no-recording':
        medium['tracks'][0].pop('recording')
    elif damage == 'wrong-position':
        medium['tracks'][0]['position'] = 2
    elif damage == 'wrong-media-count':
        full['media-count'] = 2
    elif damage == 'wrong-artist':
        full['artist-credit'] = credit('Other Artist')
    elif damage == 'wrong-group':
        full['release-group']['id'] = uid(88)
    else:
        full['date'] = '2010'
    result = network.resolve(tmp_path)
    assert result['status'] != 'matched'
    assert not any(urlsplit(c['url']).path == '/ws/2/release/' + uid(10) for c in network.calls)
    assert not any('coverartarchive' in c['url'] for c in network.calls)


@pytest.mark.parametrize('status', [404, 429, 500])
def test_missing_or_failed_release_request_is_not_negative_track_evidence(tmp_path, status):
    network, _ = ep_network()
    network.override = lambda url: response(status=status) if urlsplit(url).path == '/ws/2/release/' + uid(3) else None
    assert network.resolve(tmp_path)['status'] != 'matched'
    assert not any('coverartarchive' in c['url'] for c in network.calls)


def test_explicit_album_mismatch_does_not_switch_to_ep(tmp_path):
    network, full = ep_network()
    # Explicit album requests may use the later edition; give it a complete
    # nonmatching tracklist too, to ensure it cannot silently switch releases.
    bonus_full = copy.deepcopy(full)
    bonus_full.update(id=uid(5), date='2010')
    network.override = lambda url: response(bonus_full) if urlsplit(url).path == '/ws/2/release/' + uid(5) else None
    result = network.resolve(tmp_path, album='Original Album')
    assert result['reason'] == 'album_track_unconfirmed'
    assert not any('coverartarchive' in c['url'] for c in network.calls)


@pytest.mark.parametrize('wrong', ['track', 'artist', 'version'])
def test_ep_must_independently_verify_track_identity(tmp_path, wrong):
    network, full = ep_network()
    ep = copy.deepcopy(network.releases[uid(2)][1])
    bad = recording(88, title='Other Song') if wrong == 'track' else (
        recording(88, artist='Other Artist') if wrong == 'artist' else recording(88, disambiguation='live'))
    ep['media'] = [{'position': 1, 'track-count': 1, 'tracks': [{'position': 1, 'recording': bad}]}]
    def override(url):
        path = urlsplit(url).path
        if path == '/ws/2/release/' + uid(3):
            return response(full)
        if path == '/ws/2/release/' + uid(10):
            return response(ep)
    network.override = override
    assert network.resolve(tmp_path)['status'] != 'matched'


def test_ambiguous_eps_do_not_choose_arbitrarily(tmp_path):
    network, _ = ep_network()
    second = release(12, 13, 'Another EP', '2000')
    second['release-group']['primary-type'] = 'EP'
    network.releases[uid(2)].append(second)
    assert network.resolve(tmp_path)['reason'] == 'album_ambiguous'


def test_request_budget_exhaustion_cannot_trigger_fallback(tmp_path, monkeypatch):
    network, _ = ep_network()
    monkeypatch.setattr(artwork, 'MAX_REQUESTS', 4)
    assert network.resolve(tmp_path)['reason'] == 'lookup_budget_exceeded'
    assert not any('coverartarchive' in c['url'] for c in network.calls)


def test_edition_limit_is_reported_without_claiming_exhaustive_absence(tmp_path):
    network, full = ep_network()
    originals = [release(i, date='2001-04-03') for i in [20, 21, 22, 23]]
    network.releases[uid(99)] = originals
    def override(url):
        identifier = urlsplit(url).path.rsplit('/', 1)[-1]
        if identifier in {r['id'] for r in originals}:
            return response({**full, 'id': identifier})
    network.override = override
    result = network.resolve(tmp_path)
    assert result['album'] == 'Original EP'
    proof = result['provenance']['albumCandidateChecks'][0]
    assert proof['editionLimitReached'] is True
    assert proof['candidateEditionCount'] == 4
    assert proof['checkedReleaseIds'] == [uid(20), uid(21), uid(22)]


def test_old_negative_cache_policy_is_not_reused(tmp_path, monkeypatch):
    network, full = ep_network()
    # Seed a previous-policy failure, then restore the real policy and valid
    # complete tracklist. This also exercises invalidation of entity lookups.
    current_policy = artwork.POLICY_VERSION
    assert current_policy > 7
    with monkeypatch.context() as patch:
        patch.setattr(artwork, 'POLICY_VERSION', 7)
        full['media'][0].pop('track-count')
        assert network.resolve(tmp_path / 'old', cache=tmp_path / 'cache')['status'] != 'matched'
    full['media'][0]['track-count'] = 1
    assert network.resolve(tmp_path / 'new', cache=tmp_path / 'cache')['album'] == 'Original EP'
