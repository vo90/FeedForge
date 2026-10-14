import copy
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from feedback_converter.song_import import artwork
from test_song_import_artwork import Network, recording, response, uid


def network(title='Example Song: I. Beginning / II. Ending', alias='Example Song'):
    net = Network()
    net.records = [{**recording(title=title), 'aliases': [{'name': alias}]}]
    return net


def alias_calls(net):
    return [c for c in net.calls if parse_qs(urlsplit(c['url']).query).get('query', [''])[0].startswith('alias:')]


@pytest.mark.parametrize('title,alias', [
    ('Example Song: I. Beginning / II. Ending', 'Example Song'),
    ('A Different Name', 'Example Song'),
    ('2112: I. Overture / II. The Temples of Syrinx / III. Discovery / IV. Presentation / V. Oracle: The Dream / VI. Soliloquy / VII. Grand Finale', '2112'),
])
def test_exact_catalogue_alias_keeps_source_and_proves_original_album(tmp_path, title, alias):
    net = network(title, alias)
    before = copy.deepcopy(net.records)
    result = net.resolve(tmp_path, title=alias)
    assert result['status'] == 'matched'
    proof = result['provenance']
    assert proof['matchBasis'] == 'artist-recording-alias-version-album-track'
    assert proof['recordingAliasResolution'] == [{
        'recordingId': uid(2), 'sourceTitle': alias, 'canonicalTitle': title,
        'aliases': [alias], 'artistIds': [uid(1)], 'basis': 'exact-catalogue-recording-alias'}]
    assert proof['trackEvidence']['recordingMatch'] == 'linked-id'
    assert proof['audioRecordingVerified'] is False and len(alias_calls(net)) == 1
    assert before == net.records


def test_exact_title_does_not_add_alias_requests(tmp_path):
    net = Network()
    assert net.resolve(tmp_path)['status'] == 'matched'
    assert not alias_calls(net)
    assert not any('inc=artist-credits+aliases' in c['url'] for c in net.calls)


def test_matching_live_alias_keeps_explicit_version(tmp_path):
    net = network('A Different Name (live)', 'Example Song (live)')
    # A studio album cannot prove membership for this live recording.
    net.releases[uid(2)][0]['release-group']['secondary-types'] = ['Live']
    result = net.resolve(tmp_path, title='Example Song (live)')
    assert result['status'] == 'matched'
    assert result['provenance']['versions'] == ['live']


def test_alias_service_failure_remains_unavailable(tmp_path):
    net = network()
    net.override = lambda url: response(status=503) if 'inc=artist-credits+aliases' in url else None
    result = net.resolve(tmp_path)
    assert result['reason'] == 'service_unavailable'
    assert not any('coverartarchive.org' in c['url'] for c in net.calls)


@pytest.mark.parametrize('damage', ['prefix', 'canonical-live', 'canonical-demo', 'alias-live',
    'wrong-artist', 'missing-alias', 'malformed-list', 'malformed-name', 'wrong-id',
    'changed-title', 'changed-artist', 'missing-track', 'video'])
def test_alias_cannot_hide_wrong_identity_or_missing_track(tmp_path, damage):
    net = network()
    rec = net.records[0]
    if damage == 'prefix':rec['aliases'] = [{'name': 'Example Song: Overture'}]
    if damage == 'canonical-live':rec['title'] += ' (live)'
    if damage == 'canonical-demo':rec['disambiguation'] = 'demo'
    if damage == 'alias-live':rec['aliases'][0]['name'] += ' (live)'
    if damage == 'wrong-artist':rec['artist-credit'] = recording(artist='Other Artist')['artist-credit']
    if damage == 'missing-alias':rec.pop('aliases')
    if damage == 'malformed-list':rec['aliases'] = {'name': 'Example Song'}
    if damage == 'malformed-name':rec['aliases'] = [{'name': 12}]
    if damage == 'video':rec['video'] = True
    def override(url):
        path = urlsplit(url).path
        if path == '/ws/2/recording/' + uid(2):
            full = copy.deepcopy(rec)
            if damage == 'wrong-id':full['id'] = uid(80)
            if damage == 'changed-title':full['title'] = 'Changed title'
            if damage == 'changed-artist':full['artist-credit'] = recording(artist='Other Artist')['artist-credit']
            return response(full)
        if path == '/ws/2/release/' + uid(3) and damage == 'missing-track':
            return response({**net.releases[uid(2)][0], 'media': [{'position': 1, 'tracks': []}]})
    net.override = override
    result = net.resolve(tmp_path)
    assert result['status'] != 'matched' and 'path' not in result
    assert not any('coverartarchive.org' in c['url'] for c in net.calls)


def test_alias_cannot_override_pinned_recording_title(tmp_path):
    net = network()
    assert net.resolve(tmp_path, musicbrainzRecordingId=uid(2))['status'] != 'matched'
    assert not alias_calls(net)


def test_multiple_artist_identities_remain_ambiguous(tmp_path):
    net = network()
    other = copy.deepcopy(net.records[0]);other['id'] = uid(8)
    other['artist-credit'][0]['artist']['id'] = uid(9)
    net.records.append(other)
    assert net.resolve(tmp_path)['reason'] == 'artist_ambiguous'


def test_incomplete_alias_set_is_not_truncated_to_a_winner(tmp_path):
    net = network()
    net.records = [{**copy.deepcopy(net.records[0]), 'id': uid(i)} for i in range(10, 19)]
    assert net.resolve(tmp_path)['reason'] == 'incomplete_catalogue'
    assert not any('inc=artist-credits+aliases' in c['url'] for c in net.calls)


def test_alias_lookup_keeps_global_request_budget(tmp_path, monkeypatch):
    net = network()
    monkeypatch.setattr(artwork, 'MAX_REQUESTS', 2)
    assert net.resolve(tmp_path)['reason'] == 'lookup_budget_exceeded'
    assert len(net.calls) == 2


def test_old_missing_recording_cache_rechecked_once(tmp_path):
    from test_song_import_compilation_artwork import seed_old_result
    net = network();cache = tmp_path/'cache'
    seed_old_result(cache, net, 'recording_not_found')
    result = net.resolve(tmp_path/'after', cache=cache)
    assert result['status'] == 'matched'
    net.calls.clear()
    assert net.resolve(tmp_path/'cached', cache=cache)['status'] == 'matched'
    assert net.calls == []


def test_new_negative_cache_does_not_repeat_alias_search(tmp_path):
    net = network(alias='No match');cache = tmp_path/'cache'
    assert net.resolve(tmp_path/'before', cache=cache)['reason'] == 'recording_not_found'
    net.calls.clear()
    assert net.resolve(tmp_path/'again', cache=cache)['reason'] == 'recording_not_found'
    assert net.calls == []


def test_successful_legacy_cover_cache_stays_reusable(tmp_path):
    net = Network();cache = tmp_path/'cache'
    assert net.resolve(tmp_path/'before', cache=cache)['status'] == 'matched'
    for entry in cache.rglob('*.json'):
        saved = json.loads(entry.read_text());saved.pop('recordingAliasVersion', None)
        entry.write_text(json.dumps(saved))
    net.calls.clear()
    assert net.resolve(tmp_path/'again', cache=cache)['status'] == 'matched'
    assert net.calls == []
