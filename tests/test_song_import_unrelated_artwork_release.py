import copy
import json

import pytest

from feedback_converter.song_import import compilation_artwork as compilation
from test_song_import_artwork import uid
from test_song_import_compilation_artwork import (
    CompilationNetwork, compilation as release, seed_old_result, assert_artwork_only,
)


def unrelated():
    candidate = release(80, 81, 'Unrelated anthology')
    candidate['artist-credit'] = [{'name': 'Various Artists', 'artist': {
        'name': 'Various Artists', 'id': '89ad4ac3-39f7-470e-963a-56509c546377'}}]
    candidate['release-group'].pop('secondary-types')
    return candidate


@pytest.mark.parametrize('first', [True, False])
@pytest.mark.parametrize('artist', ['various', 'other', 'collaboration'])
def test_unrelated_malformed_release_does_not_poison_valid_compilation(tmp_path, first, artist):
    bad = unrelated()
    if artist == 'other':
        bad['artist-credit'] = [{'artist': {'id': uid(99), 'name': 'Different Artist'}}]
    elif artist == 'collaboration':
        bad['artist-credit'] = copy.deepcopy(release()['artist-credit']) + [{'artist': {'id': uid(99), 'name': 'Guest'}}]
    rows = [bad, release()] if first else [release(), bad]
    net = CompilationNetwork(rows)
    result = net.resolve(tmp_path)
    assert_artwork_only(result)
    assert all(uid(80) not in call['url'] and uid(81) not in call['url'] for call in net.calls)


def test_unrelated_release_alone_never_supplies_art(tmp_path):
    net = CompilationNetwork([unrelated()])
    result = net.resolve(tmp_path)
    assert result['status'] != 'matched' and result['reason'] == 'album_not_found'
    assert not any('coverartarchive.org' in c['url'] for c in net.calls)


@pytest.mark.parametrize('credits', [None, [], [{'artist': {'name': 'Unknown'}}],
    [{'artist': {'id': 'invalid', 'name': 'Unknown'}}],
    [{'artist': {'id': uid(99), 'name': 'Different'}}, ' & ', {'artist': {'id': uid(1), 'name': 'Example Artist'}}],
    [{'artist': 'invalid'}], release()['artist-credit']])
def test_unproven_or_same_identity_cannot_bypass_compilation_validation(credits):
    candidate = unrelated()
    candidate['artist-credit'] = credits
    with pytest.raises(compilation._LookupFailure, match='invalid_metadata'):
        compilation._eligible(candidate, (uid(1),))


def test_no_target_identity_cannot_bypass_validation():
    with pytest.raises(compilation._LookupFailure, match='invalid_metadata'):
        compilation._eligible(unrelated(), ())


def test_valid_identity_still_requires_album_type_and_track_proof(tmp_path):
    net = CompilationNetwork([unrelated(), release()])
    full = net.full_release(uid(10))
    full['media'][0]['tracks'][0]['recording']['title'] = 'Another Song'
    net.documents[uid(10)] = full
    result = net.resolve(tmp_path)
    assert result['reason'] == 'album_identity_unconfirmed'
    assert not any('coverartarchive.org' in c['url'] for c in net.calls)


def test_old_compilation_metadata_miss_rechecked_once(tmp_path):
    net = CompilationNetwork([unrelated(), release()]); cache = tmp_path/'cache'
    seed_old_result(cache, net, 'invalid_metadata', provenance={
        'compilationArtworkAttempt': {'status': 'unavailable', 'reason': 'invalid_metadata'}})
    assert_artwork_only(net.resolve(tmp_path/'first', cache=cache))
    net.calls.clear()
    assert_artwork_only(net.resolve(tmp_path/'second', cache=cache))
    assert not net.calls


def test_unrelated_metadata_cache_failure_is_not_invalidated(tmp_path):
    net = CompilationNetwork(); cache = tmp_path/'cache'
    seed_old_result(cache, net, 'invalid_metadata')
    assert net.resolve(tmp_path/'result', cache=cache)['reason'] == 'invalid_metadata'
    assert not net.calls


def test_new_compilation_metadata_miss_keeps_negative_cache(tmp_path):
    net = CompilationNetwork(); cache = tmp_path/'cache'
    # An actual potential candidate with malformed secondary metadata remains unsafe.
    candidate = release()
    candidate['release-group']['secondary-types'] = ['Compilation', 12]
    net.releases[uid(2)] = [candidate]
    result = net.resolve(tmp_path/'first', cache=cache)
    assert result['reason'] == 'invalid_metadata'
    net.calls.clear()
    assert net.resolve(tmp_path/'second', cache=cache)['reason'] == 'invalid_metadata'
    assert not net.calls
    entries = [json.loads(p.read_text()) for p in cache.glob('*.json')]
    assert any(e.get('compilationCandidateVersion') == 1 and 'result' in e for e in entries)
