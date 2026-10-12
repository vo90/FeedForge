"""Earliest overall release dates must not masquerade as Official dates."""
import copy
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from feedback_converter.song_import import artwork
from test_song_import_artwork import Network, release, response, uid


def catalogue():
    network = Network()
    original = release(date='1994')
    original['release-group']['first-release-date'] = '1993-11-02'
    earlier = release(9, date='1993-11-02', status=None)
    later = release(8, date='2014')
    network.releases = {uid(2): [original, later], uid(99): [earlier]}
    return network


def browses(network):
    return [c for c in network.calls if 'release-group=' in c['url']]


def test_unknown_status_summary_date_reconciles_to_verified_official_year(tmp_path):
    network = catalogue()
    result = network.resolve(tmp_path)
    assert result['status'] == 'matched' and result['year'] == 1994
    assert result['provenance']['trackEvidence']['releaseId'] == uid(3)
    proof = result['provenance']['releaseDateResolution']
    assert proof['reportedFirstReleaseDate'] == '1993-11-02'
    assert proof['officialDateInterval'] == ['1994-01-01', '1994-12-31']
    assert proof['officialReleaseCount'] == 2 and proof['releaseCount'] == 3
    assert proof['earliestYearReleaseIds'] == [uid(3)]
    assert len(browses(network)) == 1


def test_later_bonus_is_not_checked_when_original_official_lacks_song(tmp_path):
    network = catalogue()
    original = network.releases[uid(2)].pop(0)
    network.releases[uid(99)].append(original)
    result = network.resolve(tmp_path)
    assert result['reason'] == 'album_track_unconfirmed'
    assert not any('/ws/2/release/' + uid(8) in c['url'] for c in network.calls)
    assert not any('coverartarchive' in c['url'] for c in network.calls)


@pytest.mark.parametrize('damage', ['missing-date','invalid-date','wrong-group','wrong-artist',
                                  'wrong-type','only-reissue','no-official'])
def test_uncertain_catalogue_cannot_justify_advancing_year(tmp_path, damage):
    network = catalogue()
    def change(url):
        if 'release-group=' not in url:
            return None
        rows = copy.deepcopy([r for rs in network.releases.values() for r in rs])
        if damage == 'missing-date': rows[0].pop('date')
        elif damage == 'invalid-date': rows[0]['date'] = '1994-13'
        elif damage == 'wrong-group': rows[0]['release-group']['id'] = uid(66)
        elif damage == 'wrong-artist': rows[0]['artist-credit'][0]['artist']['id'] = uid(66)
        elif damage == 'wrong-type': rows[0]['release-group']['primary-type'] = 'EP'
        elif damage == 'only-reissue': rows = rows[:2]
        elif damage == 'no-official': rows = rows[2:]
        return response({'release-count': len(rows), 'releases': rows})
    network.override = change
    assert network.resolve(tmp_path)['status'] != 'matched'
    assert not any('coverartarchive' in c['url'] for c in network.calls)


@pytest.mark.parametrize('damage', ['count','duplicate','over-cap','missing','http'])
def test_failed_or_incomplete_browse_cannot_reconcile(tmp_path, damage):
    network = catalogue()
    def change(url):
        if 'release-group=' not in url: return None
        rows = copy.deepcopy([r for rs in network.releases.values() for r in rs])
        count = len(rows)
        if damage == 'count': count += 1
        elif damage == 'duplicate': rows[1] = rows[0]
        elif damage == 'over-cap': count = 301
        elif damage == 'missing': return response(status=404)
        elif damage == 'http': return response(status=500)
        return response({'release-count': count, 'releases': rows})
    network.override = change
    assert network.resolve(tmp_path)['status'] != 'matched'


@pytest.mark.parametrize('other_year', ['1993', '1994', '1995'])
def test_corrected_date_rechecks_competing_album_order(tmp_path, other_year):
    network = catalogue()
    # A initially appears earliest (1993-11-02), before B (1993-12 or later).
    other = release(20, 21, 'Other Album', other_year + '-12-01')
    network.releases[uid(2)].append(other)
    result = network.resolve(tmp_path)
    if other_year == '1993':
        assert result['album'] == 'Other Album' and result['year'] == 1993
    elif other_year == '1994':
        assert result['reason'] == 'album_ambiguous'
    else:
        assert result['album'] == 'Original Album' and result['year'] == 1994
    assert len(browses(network)) == 1


def test_repeated_reranking_browses_each_group_only_once(tmp_path):
    network = catalogue()
    # B moves from 1993-12 to 1995, so A (now 1994) must win on the third choice.
    other = release(20, 21, 'Other Album', '1995')
    old = release(22, 21, 'Other Album', '1993-12-01', status='Promotion')
    network.releases[uid(2)].append(other)
    network.releases[uid(99)].append(old)
    result = network.resolve(tmp_path)
    assert result['album'] == 'Original Album' and result['year'] == 1994
    assert len(browses(network)) == 2


def test_search_miss_cannot_skip_existing_official_summary_year(tmp_path):
    network = catalogue()
    network.releases[uid(99)][0]['status'] = 'Official'
    def change(url):
        query = parse_qs(urlsplit(url).query).get('query', [''])[0]
        if query.startswith('rgid:'):
            return response({'count': 0, 'releases': []})
    network.override = change
    assert network.resolve(tmp_path)['reason'] == 'album_track_unconfirmed'
    assert not any('/ws/2/release/' + uid(3) in c['url'] for c in network.calls)


def test_explicit_album_retains_reissue_permission_without_reconciliation(tmp_path):
    network = catalogue()
    result = network.resolve(tmp_path, album='Original Album')
    assert result['status'] == 'matched'
    assert not browses(network)
    assert 'releaseDateResolution' not in result['provenance']


@pytest.mark.parametrize('budget', ['MAX_REQUESTS', 'MAX_SECONDS'])
def test_reconciliation_stays_within_shared_budget(tmp_path, monkeypatch, budget):
    network = catalogue()
    monkeypatch.setattr(artwork, budget, 4)
    assert network.resolve(tmp_path)['reason'] == 'lookup_budget_exceeded'


def test_negative_cache_retries_once_without_discarding_other_results(tmp_path):
    for reason in ['album_track_unconfirmed', 'artist_ambiguous']:
        saved = {'version': artwork.POLICY_VERSION, 'createdAt': 1000, 'ttl': 86400,
                 'result': artwork._result('ambiguous', reason)}
        (tmp_path / 'key.json').write_text(json.dumps(saved))
        assert (artwork._cache_read(tmp_path, 'key', lambda: 1001) is None) == (reason == 'album_track_unconfirmed')
    artwork._cache_write(tmp_path, 'key', artwork._result('ambiguous', 'album_track_unconfirmed'), None, lambda: 1000)
    assert artwork._cache_read(tmp_path, 'key', lambda: 1001)[0]['reason'] == 'album_track_unconfirmed'
