import copy
import json
import re
from urllib.parse import parse_qs, urlsplit

import pytest

from feedback_converter.song_import import artwork as art, album_match as album
from feedback_converter.song_import import recording_pages
from test_song_import_artwork import Network, recording, release, response, uid


class Search:
    def __init__(self, count=212, overlap=True):
        self.records = [{**recording(i + 100), 'id': f'{i % 16:x}{i:07x}-0000-4000-8000-000000000000',
                         'releases': [release()]} for i in range(count)]
        self.overlap = overlap
        self.calls = []
        self.change = None

    def json(self, url, kind):
        params = parse_qs(urlsplit(url).query)
        self.calls.append(params)
        query = params['query'][0]
        prefixes = re.findall(r'([0-9a-f])\*', query)
        offset = int(params['offset'][0])
        records = [r for r in self.records if not prefixes or r['id'][0].lower() in prefixes]
        start = offset - 1 if self.overlap and offset and not prefixes else offset
        doc = {'offset': offset, 'count': len(records), 'recordings': copy.deepcopy(records[start:start + 100])}
        if offset and not prefixes and len(doc['recordings']) > len(records) - offset:
            doc['recordings'].pop()
        if self.change:
            self.change(doc, params, prefixes)
        return doc

    def run(self, **kwargs):
        return art._pages(self, 'recording', {'query': 'recording:"Example Song" AND artist:"Example Artist"'},
                          'recordings', 'count', 300, recover_recording_overlap=True, **kwargs)


@pytest.mark.parametrize('count', [0, 1, 100, 101, 126, 212, 300])
def test_healthy_pages_have_no_recovery_overhead(count):
    search = Search(count, overlap=False)
    assert search.run() == search.records
    assert len(search.calls) == max(1, (count + 99) // 100)


@pytest.mark.parametrize('count', [101, 126, 212, 300])
def test_recovers_exact_complete_set_after_overlap(count):
    search = Search(count)
    actual = search.run()
    assert {r['id'] for r in actual} == {r['id'] for r in search.records}
    assert len(actual) == count
    assert all(p['offset'] == ['0'] for p in search.calls[2:])
    assert all(p['query'][0].startswith('(recording:"Example Song" AND artist:"Example Artist") AND rid:(')
               for p in search.calls[2:])


def test_query_preserves_all_version_artist_and_title_constraints():
    search = Search(126)
    album._search(search, 'Song (Live)', 'A OR B', {'live'}, (uid(1),))
    query = search.calls[0]['query'][0]
    assert 'arid:' + uid(1) in query
    assert '-comment:live' not in query
    assert all(p['query'][0].startswith('(' + query + ') AND rid:(') for p in search.calls[2:])


@pytest.mark.parametrize('mutation', [
    lambda d: d.update(count=d['count'] + 1),
    lambda d: d.update(count=True),
    lambda d: d.pop('count'),
    lambda d: d.update(offset=1),
    lambda d: d['recordings'].pop(),
    lambda d: d['recordings'].__setitem__(1,d['recordings'][0]),
    lambda d: d['recordings'].__setitem__(0,{'id':'invalid'}),
    lambda d: d['recordings'].__setitem__(0,{'id':'f0000000-0000-4000-8000-000000000000'}),
])
def test_malformed_partitions_cannot_return_partial_results(mutation):
    search = Search(126)
    search.change = lambda doc, params, prefixes: mutation(doc) if prefixes else None
    with pytest.raises(art._LookupFailure) as exc:
        search.run()
    assert exc.value.reason == 'incomplete_catalogue'


@pytest.mark.parametrize('mutation', [
    lambda d: d.update(count=301),
    lambda d: d.update(count=d['count'] + 1),
    lambda d: d.update(offset=99),
    lambda d: d['recordings'].__setitem__(1,d['recordings'][0]),
    lambda d: d['recordings'].__setitem__(1,{'id':'invalid'}),
])
def test_malformed_second_page_does_not_trigger_recovery(mutation):
    search = Search(126)
    search.change = lambda doc, params, prefixes: mutation(doc) if params['offset']==['100'] else None
    with pytest.raises(art._LookupFailure):
        search.run()
    assert len(search.calls) == 2


def test_same_count_but_missing_observed_id_is_rejected():
    search = Search(126)
    def change(doc, params, prefixes):
        if prefixes:
            for r in doc['recordings']:
                if r['id'] == search.records[0]['id']:
                    r['id'] = '00000000-1111-4000-8000-000000000000'
    search.change = change
    with pytest.raises(art._LookupFailure):
        search.run()


def test_uuid_case_does_not_hide_overlap_or_reject_valid_partition():
    search = Search(126)
    for r in search.records:
        r['id'] = r['id'].upper()
    assert len(search.run()) == 126


def test_dense_partition_and_recovery_request_limit_fail_safely(monkeypatch):
    search = Search(126)
    for r in search.records:
        r['id'] = '0' + r['id'][1:]
    with pytest.raises(art._LookupFailure):
        search.run()
    assert len(search.calls) == 6
    monkeypatch.setattr(recording_pages, 'MAX_PARTITION_REQUESTS', 1)
    search = Search(126)
    with pytest.raises(art._LookupFailure):
        search.run()
    assert len(search.calls) == 3


def test_generic_paging_does_not_recover_recording_or_release_overlap():
    search = Search(126)
    with pytest.raises(art._LookupFailure):
        art._pages(search,'recording',{'query':'example'},'recordings','count',300)
    assert len(search.calls)==2


@pytest.mark.parametrize('ambiguous', [False, True])
def test_recovery_retains_album_track_and_artist_checks(tmp_path, ambiguous):
    search = Search(126)
    network = Network()
    network.records = search.records
    network.releases = {r['id']: [release()] for r in network.records}
    if ambiguous:
        network.records[-1]['artist-credit'][0]['artist']['id'] = uid(999)
    network.override = lambda url: response(search.json(url,'mb')) if urlsplit(url).path=='/ws/2/recording/' else None
    result = network.resolve(tmp_path)
    if ambiguous:
        assert result['reason']=='artist_ambiguous'
        assert not any('coverartarchive' in c['url'] for c in network.calls)
    else:
        assert result['status']=='matched'
        assert result['album']=='Original Album'
        assert result['provenance']['trackEvidence']['basis']=='original-year-release-track'
        assert len(result['provenance']['recordingIds'])==126


@pytest.mark.parametrize('budget', ['requests','seconds'])
def test_recovery_obeys_shared_client_budget(tmp_path, monkeypatch, budget):
    search = Search(212)
    network = Network()
    network.override = lambda url: response(search.json(url,'mb')) if urlsplit(url).path=='/ws/2/recording/' else None
    monkeypatch.setattr(art, 'MAX_REQUESTS' if budget=='requests' else 'MAX_SECONDS', 3)
    result = network.resolve(tmp_path)
    assert result['reason']=='lookup_budget_exceeded'
    assert len(network.calls)<=3


def test_only_old_incomplete_cache_is_retried_and_new_failure_still_cached(tmp_path):
    for reason in ('incomplete_catalogue','artist_ambiguous'):
        saved={'version':art.POLICY_VERSION,'createdAt':1000,'ttl':86400,
               'result':art._result('ambiguous',reason)}
        (tmp_path/'key.json').write_text(json.dumps(saved))
        result=art._cache_read(tmp_path,'key',lambda:1001)
        assert (result is None)==(reason=='incomplete_catalogue')
    art._cache_write(tmp_path,'key',art._result('ambiguous','incomplete_catalogue'),None,lambda:1000)
    assert art._cache_read(tmp_path,'key',lambda:1001)[0]['reason']=='incomplete_catalogue'


def test_recovered_cover_reuses_cache_without_search(tmp_path):
    search=Search(126)
    network=Network()
    network.records=search.records
    network.releases={r['id']:[release()] for r in network.records}
    network.override=lambda url: response(search.json(url,'mb')) if urlsplit(url).path=='/ws/2/recording/' else None
    first=network.resolve(tmp_path/'first',cache=tmp_path/'cache')
    count=len(network.calls)
    second=network.resolve(tmp_path/'second',cache=tmp_path/'cache')
    assert first['status']==second['status']=='matched'
    assert first['provenance']==second['provenance']
    assert len(network.calls)==count
