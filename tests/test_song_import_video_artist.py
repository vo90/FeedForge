"""Exact selected-video relationships disambiguate artwork, never audio masters."""
from copy import deepcopy
from urllib.parse import parse_qs, urlsplit

import pytest

from feedback_converter.song_import import artwork, video_artist_match as match
from test_song_import_artwork import Network, recording, response, uid

VIDEO = 'abcdefghijk'
OTHER_VIDEO = 'lmnopqrstuv'
URL = 'https://www.youtube.com/watch?v=' + VIDEO


def link(target, direction):
    return {'type': 'free streaming', 'type-id': match.STREAM, 'target-type': target,
            'direction': direction, 'ended': False, 'end': None}


class VideoNetwork(Network):
    def __init__(self):
        super().__init__()
        other = recording(20)
        other['artist-credit'][0]['artist']['id'] = uid(21)
        self.records.append(other)
        self.index = {'id': uid(50), 'resource': URL, 'relations': [
            {**link('recording', 'backward'), 'recording': {'id': uid(2)}}]}
        self.full = {uid(2): {**deepcopy(self.records[0]), 'video': False, 'relations': [
            {**link('url', 'forward'), 'url': {'id': uid(50), 'resource': URL}}]}}
        self.override = self.route

    def route(self, url):
        parts = urlsplit(url)
        params = parse_qs(parts.query)
        if parts.path == '/ws/2/url/':
            assert params['resource'][0].startswith('https://www.youtube.com/watch?v=')
            return response(self.index) if self.index else response(status=404)
        if parts.path.startswith('/ws/2/recording/') and 'url-rels' in params.get('inc', [''])[0]:
            r = self.full.get(parts.path.rsplit('/', 1)[1])
            return r if isinstance(r, artwork.HttpResponse) else response(r, status=200 if r else 404)
        if parts.path == '/ws/2/recording/' and 'arid:' in params.get('query', [''])[0]:
            rows = [r for r in self.records if 'arid:' + r['artist-credit'][0]['artist']['id'] in params['query'][0]]
            return response({'count': len(rows), 'recordings': rows})

    def lookup(self, path, **kwargs):
        return self.resolve(path, audioVideoId=VIDEO, **kwargs)

    def add_target(self, n, *, other=False, video=False):
        self.index['relations'].append({**link('recording', 'backward'), 'recording': {'id': uid(n)}})
        record = deepcopy(self.full[uid(2)])
        record.update(id=uid(n), video=video)
        if other:
            record['artist-credit'][0]['artist']['id'] = uid(21)
        self.full[uid(n)] = record


def test_complete_artist_search_album_proof_and_provenance(tmp_path):
    n = VideoNetwork(); before = deepcopy(n.records)
    result = n.lookup(tmp_path)
    assert result['reason'] == 'album_cover'
    proof = result['provenance']['artistVideoResolution']
    assert proof['artistIds'] == [uid(1)] and proof['videoId'] == VIDEO
    assert result['provenance']['audioRecordingVerified'] is False
    assert result['provenance']['trackEvidence']['recordingId'] == uid(2)
    assert any('arid:' + uid(1) in parse_qs(urlsplit(c['url']).query).get('query', [''])[0] for c in n.calls)
    assert n.records == before


@pytest.mark.parametrize('video', [None, '', 'bad', 'abcdefghijk?x=1', 'https://example.com', '/../../file'])
def test_invalid_video_never_queried(tmp_path, video):
    n = VideoNetwork()
    assert n.resolve(tmp_path, audioVideoId=video)['reason'] == 'artist_ambiguous'
    assert len(n.calls) == 1


def test_missing_url_is_not_permission_to_guess(tmp_path):
    n = VideoNetwork(); n.index = None
    assert n.lookup(tmp_path)['reason'] == 'artist_ambiguous'
    assert len(n.calls) == 2


@pytest.mark.parametrize('mutation', ['resource', 'index_id', 'relations', 'missing_id', 'wrong_direction',
    'unknown_end', 'wrong_type_label', 'missing_record', 'wrong_record_id', 'backlink', 'wrong_backlink',
    'video_unknown', 'null_url', 'bad_credit', 'bad_relation'])
def test_malformed_or_inconsistent_evidence(tmp_path, mutation):
    n = VideoNetwork(); rel = n.index['relations'][0]; r = n.full[uid(2)]
    if mutation == 'resource': n.index['resource'] = URL + 'x'
    elif mutation == 'index_id': n.index['id'] = 'invalid'
    elif mutation == 'relations': n.index['relations'] = None
    elif mutation == 'missing_id': rel['recording']['id'] = ''
    elif mutation == 'wrong_direction': rel['direction'] = 'forward'
    elif mutation == 'unknown_end': rel.pop('ended')
    elif mutation == 'wrong_type_label': rel['type'] = 'other'
    elif mutation == 'missing_record': n.full[uid(2)] = None
    elif mutation == 'wrong_record_id': r['id'] = uid(99)
    elif mutation == 'backlink': r['relations'] = []
    elif mutation == 'wrong_backlink': r['relations'][0]['direction'] = 'backward'
    elif mutation == 'video_unknown': r.pop('video')
    elif mutation == 'null_url': r['relations'][0]['url'] = None
    elif mutation == 'bad_credit': r['artist-credit'] = [None]
    elif mutation == 'bad_relation': r['relations'].append(None)
    assert n.lookup(tmp_path)['reason'] == 'artist_ambiguous'


@pytest.mark.parametrize('mutation', ['ended', 'video_only', 'wrong_title', 'live'])
def test_no_compatible_audio_proof(tmp_path, mutation):
    n = VideoNetwork(); r = n.full[uid(2)]
    if mutation == 'ended': n.index['relations'][0]['ended'] = True
    elif mutation == 'video_only': r['video'] = True
    elif mutation == 'wrong_title': r['title'] = 'Different Song'
    elif mutation == 'live': r['disambiguation'] = 'live'
    assert n.lookup(tmp_path)['reason'] == 'artist_ambiguous'


def test_multiple_same_artist_recordings_and_video_are_consistent(tmp_path):
    n = VideoNetwork(); n.add_target(22); n.add_target(23, video=True)
    assert n.lookup(tmp_path)['reason'] == 'album_cover'


@pytest.mark.parametrize('video', [False, True])
def test_conflicting_linked_artist_blocks_match(tmp_path, video):
    n = VideoNetwork(); n.add_target(22, other=True, video=video)
    assert n.lookup(tmp_path)['reason'] == 'artist_ambiguous'


def test_target_cap_does_not_silently_truncate(tmp_path):
    n = VideoNetwork()
    for i in range(30, 34): n.add_target(i)
    assert n.lookup(tmp_path)['reason'] == 'artist_ambiguous'
    assert len(n.calls) == 2


def test_inconsistent_duplicate_target_blocks_match(tmp_path):
    n = VideoNetwork(); rel = deepcopy(n.index['relations'][0]); rel['recording']['title'] = 'Other'
    n.index['relations'].append(rel)
    assert n.lookup(tmp_path)['reason'] == 'artist_ambiguous'


@pytest.mark.parametrize('status,expected', [(404, 'artist_ambiguous'), (503, 'service_unavailable'),
                                         (307, 'invalid_redirect')])
def test_partial_service_failure_cannot_choose_artist(tmp_path, status, expected):
    n = VideoNetwork(); n.add_target(22)
    n.full[uid(22)] = response(status=status)
    assert n.lookup(tmp_path)['reason'] == expected


@pytest.mark.parametrize('limit,value', [('MAX_REQUESTS', 2), ('MAX_SECONDS', 1.5)])
def test_existing_budget_limits_apply(tmp_path, monkeypatch, limit, value):
    n = VideoNetwork(); monkeypatch.setattr(artwork, limit, value)
    assert n.lookup(tmp_path)['reason'] == 'lookup_budget_exceeded'


def test_unambiguous_song_and_explicit_id_add_no_video_queries(tmp_path):
    n = VideoNetwork()
    assert n.lookup(tmp_path / 'explicit', musicbrainzRecordingId=uid(2))['reason'] == 'album_cover'
    assert not any('/url/' in c['url'] for c in n.calls)
    n.records.pop(); n.calls.clear()
    assert n.lookup(tmp_path / 'ordinary')['reason'] == 'album_cover'
    assert not any('/url/' in c['url'] for c in n.calls)


def test_cache_is_bound_to_selected_video(tmp_path):
    n = VideoNetwork(); cache = tmp_path / 'cache'
    assert n.lookup(tmp_path / 'first', cache=cache)['reason'] == 'album_cover'
    n.calls.clear()
    assert n.lookup(tmp_path / 'again', cache=cache)['reason'] == 'album_cover'
    assert not n.calls
    n.index = None
    assert n.resolve(tmp_path / 'different', cache=cache, audioVideoId=OTHER_VIDEO)['reason'] == 'artist_ambiguous'
    assert any('/url/' in c['url'] for c in n.calls)
    n.calls.clear()
    assert n.resolve(tmp_path / 'negative-hit', cache=cache, audioVideoId=OTHER_VIDEO)['reason'] == 'artist_ambiguous'
    assert not n.calls
    assert n.resolve(tmp_path / 'no-video', cache=cache)['reason'] == 'artist_ambiguous'


def test_old_ambiguous_cache_retried_once_with_video(tmp_path):
    n = VideoNetwork(); cache = tmp_path / 'cache'
    assert n.resolve(tmp_path / 'old', cache=cache)['reason'] == 'artist_ambiguous'
    n.calls.clear()
    assert n.lookup(tmp_path / 'new', cache=cache)['reason'] == 'album_cover'
    assert any('/url/' in c['url'] for c in n.calls)


def test_old_independent_success_keeps_cover_without_requests(tmp_path):
    n = VideoNetwork(); n.records.pop(); cache = tmp_path / 'cache'
    original = n.resolve(tmp_path / 'old', cache=cache)
    n.calls.clear()
    result = n.lookup(tmp_path / 'new', cache=cache)
    assert result['provenance']['imageHash'] == original['provenance']['imageHash']
    assert not n.calls


def test_no_matching_artist_specific_recordings_stays_unresolved(tmp_path):
    n = VideoNetwork(); route = n.route
    n.override = lambda url: response({'count': 0, 'recordings': []}) if 'arid%3A' in url else route(url)
    assert n.lookup(tmp_path)['reason'] == 'artist_ambiguous'


def test_original_song_fallback_does_not_reuse_video_identity(tmp_path, monkeypatch):
    from feedback_converter.song_import import artwork_fallback
    seen = []
    def select(client, values):
        seen.append(values)
        raise artwork._LookupFailure('artist_ambiguous', 'ambiguous')
    monkeypatch.setattr(artwork_fallback, 'select_album', select)
    values = {'artist': 'Example Artist', 'title': 'Example Song (Live)', 'album': '', 'audioTitle': '', 'audioVideoId': VIDEO}
    specific = {'reason': 'recording_not_found', 'status': 'unavailable', 'provenance': {}}
    result, raw = artwork_fallback.original_song_cover(None, values, specific, None, lambda: 1)
    assert raw is None and result['reason'] == 'recording_not_found'
    assert 'audioVideoId' not in seen[0]


def test_linked_artist_must_be_an_existing_ambiguous_candidate(tmp_path):
    n = VideoNetwork()
    n.full[uid(2)]['artist-credit'][0]['artist']['id'] = uid(99)
    assert n.lookup(tmp_path)['reason'] == 'artist_ambiguous'
    assert not any('arid%3A' in c['url'] for c in n.calls)


def test_incomplete_artist_specific_search_cannot_choose_album(tmp_path):
    n = VideoNetwork(); route = n.route
    n.override = lambda url: response({'count': 301, 'recordings': [n.records[0]]}) if 'arid%3A' in url else route(url)
    assert n.lookup(tmp_path)['reason'] == 'incomplete_catalogue'
    assert not any('coverartarchive.org' in c['url'] for c in n.calls)
