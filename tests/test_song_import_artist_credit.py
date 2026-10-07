import copy
import hashlib
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from feedback_converter.song_import.artwork import POLICY_VERSION, resolve_album_art
from test_song_import_artwork import Network, credit, recording, release, response, uid


GROUP = 'Example Artist featuring The Guests'


def named_group():
    network = Network()
    network.records = [recording(artist=GROUP)]
    network.releases = {uid(2): [release(artist=GROUP)]}
    member = {'id': uid(1), 'name': 'Example Artist', 'type': 'Person'}
    group = {'id': uid(9876), 'name': GROUP, 'type': 'Group', 'relations': [
        {'type': 'member of band', 'direction': 'backward', 'begin': '1998', 'end': None, 'artist': member}]}
    network.override = lambda url: response(group) if urlsplit(url).path == '/ws/2/artist/' + uid(9876) else None
    return network, group


def queries(network):
    return [parse_qs(urlsplit(c['url']).query).get('query', [''])[0] for c in network.calls]


def test_named_featured_group_uses_membership_and_exact_album_track(tmp_path):
    network, _ = named_group()
    metadata = {'artist': 'Example Artist', 'title': 'Example Song', 'audioTitle': 'Example Artist - Example Song'}
    before = copy.deepcopy(metadata)
    result = resolve_album_art(metadata, tmp_path, transport=network, clock=network.clock,
                               sleep=network.clock.sleep, now=network.clock)
    assert result['status'] == 'matched' and result['album'] == 'Original Album'
    assert metadata == before
    proof = result['provenance']['artistCreditResolution']
    assert proof['basis'] == 'named-group-member'
    assert proof['primaryArtistId'] == uid(1)
    assert proof['creditedArtistIds'] == [uid(9876)]
    assert proof['relationship']['begin'] == '1998'
    assert result['provenance']['trackEvidence']['recordingId'] == uid(2)
    assert any('arid:' + uid(9876) in q for q in queries(network))
    assert not any('/artist/?' in c['url'] for c in network.calls), 'do not pick a same-named artist globally'


@pytest.mark.parametrize('marker', ['feat.', 'ft.', 'FEATURING'])
def test_explicit_feature_markers_supported(tmp_path, marker):
    network, group = named_group()
    group['name'] = 'Example Artist ' + marker + ' The Guests'
    network.records[0]['artist-credit'][0]['artist']['name'] = group['name']
    assert network.resolve(tmp_path)['status'] == 'matched'


@pytest.mark.parametrize('change', ['no_relation', 'wrong_direction', 'unrelated_person', 'bad_id',
                                  'wrong_entity', 'wrong_type', 'wrong_canonical_name', 'ambiguous_member'])
def test_unproven_named_group_is_not_accepted(tmp_path, change):
    network, group = named_group()
    relation = group['relations'][0]
    if change == 'no_relation': group['relations'] = []
    elif change == 'wrong_direction': relation['direction'] = 'forward'
    elif change == 'unrelated_person': relation['artist']['name'] = 'Another Artist'
    elif change == 'bad_id': relation['artist']['id'] = 'not-a-mbid'
    elif change == 'wrong_entity': group['id'] = uid(555)
    elif change == 'wrong_type': group['type'] = 'Person'
    elif change == 'wrong_canonical_name': group['name'] = 'Unrelated Band'
    elif change == 'ambiguous_member':
        group['relations'].append({**relation, 'artist': {**relation['artist'], 'id': uid(555)}})
    result = network.resolve(tmp_path)
    assert result['status'] != 'matched'
    assert not any('coverartarchive' in c['url'] for c in network.calls)


@pytest.mark.parametrize('name', ['Example Artist Tribute featuring The Guests', 'The Example Artist featuring Guests',
                                'Example Artists featuring Guests', 'Example Artist & The Guests', 'Unrelated Band'])
def test_no_prefix_fuzzy_or_arbitrary_band_member_match(tmp_path, name):
    network, group = named_group()
    group['name'] = name
    network.records[0]['artist-credit'][0]['artist']['name'] = name
    network.records[0]['artist-credit'][0]['name'] = name
    assert network.resolve(tmp_path)['status'] != 'matched'
    assert not any('/artist/' in c['url'] for c in network.calls)


@pytest.mark.parametrize('title', ['Different Song', 'Example Song (Live)', 'Example Song (commentary)', 'Example Song (Remix)'])
def test_other_titles_and_versions_do_not_get_identity_fallback(tmp_path, title):
    network, _ = named_group()
    network.records[0]['title'] = title
    assert network.resolve(tmp_path)['status'] != 'matched'
    assert not any('/artist/' in c['url'] for c in network.calls)


def test_requery_cannot_substitute_another_artist(tmp_path):
    network, _ = named_group()
    previous = network.override
    def mismatch(url):
        if '/recording/?' in url and 'arid' in url:
            other = recording(artist='Wrong Artist')
            other['artist-credit'][0]['artist']['id'] = uid(555)
            return response({'count': 1, 'recordings': [other]})
        return previous(url)
    network.override = mismatch
    assert network.resolve(tmp_path)['status'] != 'matched'


def test_group_member_proof_does_not_replace_album_track_proof(tmp_path):
    network, _ = named_group()
    previous = network.override
    def override(url):
        if urlsplit(url).path == '/ws/2/release/' + uid(3):
            return response({**release(artist=GROUP), 'media': [{'tracks': [{'recording': recording(99, title='Other Song', artist=GROUP)}]}]})
        return previous(url)
    network.override = override
    assert network.resolve(tmp_path)['reason'] == 'album_track_unconfirmed'


def test_group_resolution_keeps_competing_album_ambiguity(tmp_path):
    network, _ = named_group()
    network.releases[uid(2)].append(release(10, 11, 'Other Album', '2001', artist=GROUP))
    assert network.resolve(tmp_path)['reason'] == 'album_ambiguous'


def test_two_proven_groups_remain_ambiguous(tmp_path):
    network, first = named_group()
    second = copy.deepcopy(first)
    second['id'] = uid(333)
    second['name'] = 'Example Artist featuring Another Group'
    second_record = recording(99, artist=second['name'])
    second_record['artist-credit'][0]['artist']['id'] = second['id']
    network.records.append(second_record)
    groups = {g['id']: g for g in (first, second)}
    network.override = lambda url: response(groups[urlsplit(url).path.rsplit('/', 1)[-1]]) if '/ws/2/artist/' in url else None
    assert network.resolve(tmp_path)['reason'] == 'artist_ambiguous'


def test_credit_candidate_budget_does_not_pick_partial_winner(tmp_path):
    network, _ = named_group()
    for i in range(3):
        rec = recording(30 + i, artist=f'Example Artist featuring Guest {i}')
        rec['artist-credit'][0]['artist']['id'] = uid(40 + i)
        network.records.append(rec)
    assert network.resolve(tmp_path)['reason'] == 'artist_ambiguous'
    assert not any('/artist/' in c['url'] for c in network.calls)


def test_structured_feature_credit_verifies_primary_artist(tmp_path):
    network = Network()
    credits = credit() + credit('Guest')
    credits[0]['joinphrase'] = ' feat. '
    network.records[0]['artist-credit'] = copy.deepcopy(credits)
    network.releases[uid(2)][0]['artist-credit'] = copy.deepcopy(credits)
    result = network.resolve(tmp_path)
    assert result['status'] == 'matched'
    assert result['provenance']['artistCreditResolution']['basis'] == 'primary-featured-credit'
    assert any('arid:' + uid(1) in q and 'arid:' + uid(9876) in q for q in queries(network))
    assert network.resolve(tmp_path / 'guest', artist='Guest')['status'] != 'matched'


def test_short_credit_does_not_override_a_recording_id(tmp_path):
    network, _ = named_group()
    assert network.resolve(tmp_path, musicbrainzRecordingId=uid(2))['status'] != 'matched'


def test_existing_exact_match_needs_no_extra_identity_request(tmp_path):
    network = Network()
    assert network.resolve(tmp_path)['status'] == 'matched'
    assert not any('/artist/' in c['url'] for c in network.calls)


def test_old_negative_cache_cannot_hide_new_resolution(tmp_path):
    network, _ = named_group()
    cache = tmp_path / 'cache'
    cache.mkdir()
    values = {'artist': 'Example Artist', 'title': 'Example Song', 'album': '', 'audioTitle': ''}
    key = hashlib.sha256(json.dumps([3, values], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    (cache / (key + '.json')).write_text(json.dumps({'version': 3, 'createdAt': network.clock(), 'ttl': 86400,
                                                   'result': {'status': 'unavailable', 'reason': 'recording_not_found'}}))
    result = network.resolve(tmp_path / 'out', cache=cache)
    assert POLICY_VERSION > 3 and result['status'] == 'matched'
    count = len(network.calls)
    again = network.resolve(tmp_path / 'cached', cache=cache)
    assert again['provenance']['artistCreditResolution'] == result['provenance']['artistCreditResolution']
    assert len(network.calls) == count
