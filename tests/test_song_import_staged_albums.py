"""Album preference must precede the candidate cap without guessing identity."""
import copy
import json
from urllib.parse import urlsplit

import pytest

from feedback_converter.song_import import artwork
from test_song_import_artwork import Network, credit, release, response, uid
from test_song_import_ep_artwork import ep_network


def many_eps(network, count=13):
    for index in range(count):
        candidate = release(100 + index, 200 + index, f'EP {index}', '1990')
        candidate['release-group']['primary-type'] = 'EP'
        network.releases[uid(2)].append(candidate)


def group_requests(network):
    return [urlsplit(c['url']).path.rsplit('/', 1)[1] for c in network.calls
            if '/ws/2/release-group/' in c['url']]


@pytest.mark.parametrize('embedded', [False, True])
def test_one_album_many_eps_verifies_only_album(tmp_path, embedded):
    network = Network()
    many_eps(network)
    if embedded:
        network.records[0]['releases'] = copy.deepcopy(network.releases[uid(2)])
    result = network.resolve(tmp_path)
    assert result['status'] == 'matched'
    assert result['album'] == 'Original Album'
    assert result['provenance']['trackEvidence']['releaseId'] == uid(3)
    assert group_requests(network) == [uid(4)]


@pytest.mark.parametrize('kind', ['Album', 'EP'])
def test_more_than_eight_needed_candidates_still_rejected(tmp_path, kind):
    network = Network()
    network.releases[uid(2)] = []
    many_eps(network, 9)
    for rel in network.releases[uid(2)]:
        rel['release-group']['primary-type'] = kind
    result = network.resolve(tmp_path)
    assert result['reason'] == 'album_ambiguous'
    assert group_requests(network) == []


def test_competing_albums_do_not_fall_back_to_ep(tmp_path):
    network = Network()
    network.releases[uid(2)].append(release(8, 9, 'Other Album', '2001-04-03'))
    many_eps(network)
    assert network.resolve(tmp_path)['reason'] == 'album_ambiguous'
    assert group_requests(network) == [uid(4), uid(9)]


def test_checked_album_mismatch_still_enforces_ep_limit(tmp_path):
    network, _ = ep_network()
    many_eps(network)
    assert network.resolve(tmp_path)['reason'] == 'album_ambiguous'
    assert group_requests(network) == [uid(4)]


def test_inconclusive_album_does_not_try_eps(tmp_path):
    network, full = ep_network()
    full['media'][0].pop('track-count')
    many_eps(network)
    assert network.resolve(tmp_path)['reason'] == 'album_track_unconfirmed'
    assert group_requests(network) == [uid(4)]


def test_ep_fallback_after_album_excluded_by_verified_metadata(tmp_path):
    network = Network()
    many_eps(network, 1)
    def change(url):
        if urlsplit(url).path == '/ws/2/release-group/' + uid(4):
            return response({**release()['release-group'], 'artist-credit': credit(),
                             'secondary-types': ['Compilation']})
    network.override = change
    result = network.resolve(tmp_path)
    assert result['status'] == 'matched' and result['album'] == 'EP 0'
    assert group_requests(network) == [uid(4), uid(200)]


@pytest.mark.parametrize('conflict', ['embedded', 'full'])
def test_conflicting_release_types_fail_without_cover(tmp_path, conflict):
    network = Network()
    if conflict == 'embedded':
        other = release(8)
        other['release-group']['primary-type'] = 'EP'
        network.releases[uid(2)].append(other)
    else:
        full = {**release()['release-group'], 'artist-credit': credit(), 'primary-type': 'EP'}
        network.override = lambda url: response(full) if '/ws/2/release-group/' in url else None
    assert network.resolve(tmp_path)['reason'] == 'album_identity_unconfirmed'
    assert not any('coverartarchive' in c['url'] for c in network.calls)


def test_explicit_ep_is_not_overridden_by_album_preference(tmp_path):
    network = Network()
    many_eps(network)
    result = network.resolve(tmp_path, album='EP 12')
    assert result['status'] == 'matched' and result['album'] == 'EP 12'
    assert group_requests(network) == [uid(212)]


def test_case_insensitive_type_keeps_album_preference(tmp_path):
    network = Network()
    network.releases[uid(2)][0]['release-group']['primary-type'] = 'album'
    many_eps(network)
    assert network.resolve(tmp_path)['album'] == 'Original Album'
    assert group_requests(network) == [uid(4)]


def test_live_album_track_mismatch_never_triggers_ep_fallback(tmp_path):
    network, _ = ep_network()
    network.records[0]['disambiguation'] = 'live'
    for rows in network.releases.values():
        for rel in rows:
            rel['release-group']['secondary-types'] = ['Live']
    result = network.resolve(tmp_path, title='Example Song (Live)')
    assert result['reason'] == 'album_track_unconfirmed'
    assert group_requests(network) == [uid(4)]


def test_inferred_label_compares_same_name_album_and_ep(tmp_path):
    from feedback_converter.song_import.album_match import select_album
    network = Network()
    other = release(8, 9, 'Original Album')
    other['release-group']['primary-type'] = 'EP'
    network.releases[uid(2)].append(other)
    client = artwork._Client(network, None, network.clock, network.clock.sleep, network.clock)
    with pytest.raises(artwork._LookupFailure) as exc:
        select_album(client, {'artist': 'Example Artist', 'title': 'Example Song'}, album_hint='Original Album')
    assert exc.value.reason == 'album_ambiguous'
    assert group_requests(network) == [uid(4), uid(9)]


@pytest.mark.parametrize('budget', ['MAX_REQUESTS', 'MAX_SECONDS'])
def test_staged_lookup_keeps_global_budget(tmp_path, monkeypatch, budget):
    network = Network()
    many_eps(network)
    monkeypatch.setattr(artwork, budget, 3)
    assert network.resolve(tmp_path)['reason'] == 'lookup_budget_exceeded'
    assert len(network.calls) <= 3


def test_only_old_ambiguous_candidate_results_retry_once(tmp_path):
    for reason in ('album_ambiguous', 'artist_ambiguous', 'album_track_unconfirmed'):
        saved = {'version': artwork.POLICY_VERSION, 'createdAt': 1000, 'ttl': 86400,
                 'officialReleaseDateVersion': 1,
                 'result': artwork._result('ambiguous', reason)}
        (tmp_path / 'key.json').write_text(json.dumps(saved))
        assert (artwork._cache_read(tmp_path, 'key', lambda: 1001) is None) == (reason == 'album_ambiguous')
    artwork._cache_write(tmp_path, 'key', artwork._result('ambiguous', 'album_ambiguous'), None, lambda: 1000)
    assert artwork._cache_read(tmp_path, 'key', lambda: 1001)[0]['reason'] == 'album_ambiguous'


def test_old_successful_cover_cache_still_reused(tmp_path):
    network = Network()
    cache = tmp_path / 'cache'
    result = network.resolve(tmp_path / 'first', cache=cache)
    assert result['status'] == 'matched'
    for path in cache.glob('*.json'):
        saved = json.loads(path.read_text())
        saved.pop('albumCandidateVersion', None)
        path.write_text(json.dumps(saved))
    count = len(network.calls)
    second = network.resolve(tmp_path / 'second', cache=cache)
    assert second['provenance'] == result['provenance']
    assert len(network.calls) == count
