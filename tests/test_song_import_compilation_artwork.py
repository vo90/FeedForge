"""Compilation covers are verified artwork evidence, never song album metadata."""
import copy
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit
import zipfile

import pytest
import yaml

from feedback_converter.song_import import artwork, worker
from test_song_import_artwork import Network, credit, recording, release, response, uid
from test_song_import_artwork_overlap import import_job  # noqa: F401


def compilation(number=10, group=11, title="Artist Collection", date="1997"):
    return release(number, group, title, date, ["Compilation"])


class CompilationNetwork(Network):
    """Offline catalogue with complete release tracks and independent cover URLs."""

    def __init__(self, releases=None):
        super().__init__()
        self.releases = {uid(2): releases if releases is not None else [compilation()]}
        self.documents = {}
        self.group_documents = {}
        self.missing_covers = set()
        self.invalid_images = set()
        self.fault = None
        self.override = self._answer

    def full_release(self, identifier):
        selected = next(r for rows in self.releases.values() for r in rows if r['id'] == identifier)
        linked = [record for record in self.records
                  if any(r['id'] == identifier for r in self.releases.get(record['id'], []))]
        tracks = [{'position': i + 1, 'recording': copy.deepcopy(record)}
                  for i, record in enumerate(linked)]
        return {**copy.deepcopy(selected), 'media-count': 1,
                'media': [{'position': 1, 'track-count': len(tracks), 'tracks': tracks}]}

    def _answer(self, url):
        if self.fault:
            replaced = self.fault(url)
            if replaced is not None:
                return replaced
        parts = urlsplit(url)
        identifier = parts.path.rsplit('/', 1)[-1]
        if parts.hostname == 'musicbrainz.org':
            if parts.path.startswith('/ws/2/release-group/') and identifier in self.group_documents:
                return response(self.group_documents[identifier])
            if parts.path.startswith('/ws/2/release/') and identifier:
                return response(self.documents.get(identifier) or self.full_release(identifier))
        if parts.hostname == 'coverartarchive.org':
            rows = [r for releases in self.releases.values() for r in releases]
            selected = next(r for r in rows if (r['release-group']['id'] if '/release-group/' in parts.path
                                               else r['id']) == identifier)
            group = selected['release-group']['id']
            if group in self.missing_covers:
                return response(status=404)
            return response({'release': 'https://musicbrainz.org/release/' + selected['id'], 'images': [
                {'id': group, 'front': True, 'approved': True,
                 'image': f'https://archive.org/download/{group}/original.png',
                 'thumbnails': {'1200': f'https://archive.org/download/{group}/front.png'}}]})
        if parts.hostname == 'archive.org' and any(group in parts.path for group in self.invalid_images):
            return response(raw=b'not an image', headers={'Content-Type': 'image/png'})
        return None


def cover_calls(network):
    return [c for c in network.calls if urlsplit(c['url']).hostname == 'coverartarchive.org']


def recording_searches(network):
    return [c for c in network.calls if urlsplit(c['url']).path == '/ws/2/recording/']


def assert_artwork_only(result, album='Artist Collection', year=1997):
    assert result['status'] == 'matched' and result['reason'] == 'compilation_album_cover'
    assert not {'album', 'year', 'albumStatus'} & result.keys()
    proof = result['provenance']
    assert proof['matchingScope'] == 'compilation-artwork'
    assert proof['audioRecordingVerified'] is False
    assert proof['compilationArtwork']['album'] == album
    assert proof['compilationArtwork']['year'] == year
    assert proof['compilationArtwork']['trackEvidence']['recordingId'] == uid(2)
    assert 'compilation' in result['message'].lower()
    assert album in result['message']
    assert Path(result['path']).is_file()


def test_compilation_only_cover_is_explicit_immutable_and_cached(tmp_path):
    network = CompilationNetwork()
    metadata = {'artist': 'Example Artist', 'title': 'Example Song', 'year': 2025,
                'audioTitle': 'Example Artist - Example Song - Guitar Lesson', 'extra': {'value': [1]}}
    before = copy.deepcopy(metadata)
    options = dict(transport=network, clock=network.clock, sleep=network.clock.sleep, now=network.clock)
    first = artwork.resolve_album_art(metadata, tmp_path / 'first', tmp_path / 'cache', **options)
    assert_artwork_only(first)
    assert metadata == before
    assert len(recording_searches(network)) == 1, 'the fallback must reuse verified recording discovery'
    calls = len(network.calls)
    second = artwork.resolve_album_art(metadata, tmp_path / 'second', tmp_path / 'cache', **options)
    assert_artwork_only(second)
    assert len(network.calls) == calls
    assert second['provenance'] == first['provenance']
    assert second['message'] == first['message']
    assert second['path'] != first['path']
    assert Path(second['path']).read_bytes() == Path(first['path']).read_bytes()
    assert metadata == before


@pytest.mark.parametrize('primary_type', ['Album', 'EP'])
def test_ordinary_album_or_ep_cover_always_wins(tmp_path, primary_type):
    original = release()
    original['release-group']['primary-type'] = primary_type
    network = CompilationNetwork([compilation(date='1990'), original])
    result = network.resolve(tmp_path)
    assert result['reason'] == 'album_cover' and result['album'] == 'Original Album'
    assert 'compilationArtwork' not in result['provenance']
    assert not any(uid(11) in c['url'] for c in cover_calls(network))


def test_original_song_cover_is_preferred_to_compilation_for_explicit_version(tmp_path):
    network = CompilationNetwork([compilation(date='1990'), release()])
    result = network.resolve(tmp_path, title='Example Song (Acoustic)')
    assert result['reason'] == 'original_song_album_cover'
    assert result['provenance']['originalSongArtwork']['album'] == 'Original Album'
    assert not any(uid(11) in c['url'] for c in cover_calls(network))


def test_complete_browse_recovers_original_omitted_from_embedded_compilation_sample(tmp_path):
    network = CompilationNetwork([compilation(), release()])
    network.records[0]['releases'] = [copy.deepcopy(network.releases[uid(2)][0])]
    result = network.resolve(tmp_path)
    assert result['reason'] == 'album_cover' and result['album'] == 'Original Album'
    assert not any(uid(11) in c['url'] for c in cover_calls(network))
    assert len(recording_searches(network)) == 1


def test_compilation_after_original_song_miss_retains_version_context(tmp_path):
    network = CompilationNetwork()
    result = network.resolve(tmp_path, title='Example Song (Acoustic)')
    assert_artwork_only(result)
    assert 'recording_not_found' in json.dumps(result['provenance'])
    assert 'album_not_found' in json.dumps(result['provenance'])
    assert len(recording_searches(network)) == 2


def test_verified_specific_album_metadata_survives_compilation_image_fallback(tmp_path):
    network = CompilationNetwork([release(), compilation()])
    network.missing_covers.add(uid(4))
    result = network.resolve(tmp_path)
    assert result['reason'] == 'compilation_album_cover'
    assert result['albumStatus'] == 'matched'
    assert (result['album'], result['year']) == ('Original Album', 2001)
    assert result['provenance']['compilationArtwork']['album'] == 'Artist Collection'


@pytest.mark.parametrize('wrong', ['recording-artist', 'recording-title', 'recording-version',
                                 'release-artist', 'same-name-different-id', 'various-artists',
                                 'co-credit', 'missing-release-credit', 'group-artist', 'ambiguous-artist'])
def test_artist_and_song_identity_cannot_be_substituted(tmp_path, wrong):
    network = CompilationNetwork()
    nominated = network.releases[uid(2)][0]
    if wrong == 'recording-artist':
        network.records = [recording(artist='Other Performer')]
    elif wrong == 'recording-title':
        network.records = [recording(title='Another Song')]
    elif wrong == 'recording-version':
        network.records = [recording(disambiguation='live')]
    elif wrong == 'release-artist':
        nominated['artist-credit'] = credit('Other Performer')
    elif wrong == 'same-name-different-id':
        nominated['artist-credit'][0]['artist']['id'] = uid(91)
    elif wrong == 'various-artists':
        nominated['artist-credit'] = credit('Various Artists')
        nominated['artist-credit'][0]['artist']['id'] = '89ad4ac3-39f7-470e-963a-56509c546377'
    elif wrong == 'co-credit':
        nominated['artist-credit'] += credit('Guest Performer')
    elif wrong == 'missing-release-credit':
        nominated.pop('artist-credit')
    elif wrong == 'group-artist':
        network.group_documents[uid(11)] = {**nominated['release-group'], 'artist-credit': credit('Other Performer')}
    else:
        duplicate = recording(90)
        duplicate['artist-credit'][0]['artist']['id'] = uid(91)
        network.records.append(duplicate)
        network.releases[uid(90)] = [compilation(92, 93)]
    result = network.resolve(tmp_path)
    assert result['status'] != 'matched' and 'path' not in result
    assert not cover_calls(network)


@pytest.mark.parametrize('secondary', ['Live', 'Remix', 'DJ-mix', 'Mixtape/Street', 'Demo',
                                     'Interview', 'Audiobook', 'Spokenword'])
def test_compilation_does_not_relax_other_release_type_exclusions(tmp_path, secondary):
    candidate = compilation()
    candidate['release-group']['secondary-types'].append(secondary)
    network = CompilationNetwork([candidate])
    result = network.resolve(tmp_path)
    assert result['status'] != 'matched'
    assert not cover_calls(network)


@pytest.mark.parametrize('status', ['Bootleg', 'Pseudo-Release', 'Promotion', ''])
def test_only_official_compilations_qualify(tmp_path, status):
    candidate = compilation()
    candidate['status'] = status
    network = CompilationNetwork([candidate])
    assert network.resolve(tmp_path)['status'] != 'matched'
    assert not cover_calls(network)


@pytest.mark.parametrize('damage', ['wrong-title', 'wrong-artist', 'wrong-version', 'missing-count',
                                  'truncated', 'missing-recording', 'wrong-position', 'wrong-media-count',
                                  'wrong-release-id', 'wrong-group', 'wrong-release-credit', 'wrong-status',
                                  'wrong-primary-type', 'extra-live-type', 'missing-compilation-type',
                                  'track-title', 'track-credit', 'wrong-year'])
def test_linked_recording_id_never_overrides_contradictory_or_incomplete_full_release(tmp_path, damage):
    network = CompilationNetwork()
    full = network.full_release(uid(10))
    medium = full['media'][0]
    track = medium['tracks'][0]
    # Keep the linked recording ID: it must not override independently bad evidence.
    if damage == 'wrong-title':
        track['recording']['title'] = 'Another Song'
    elif damage == 'wrong-artist':
        track['recording']['artist-credit'] = credit('Other Performer')
    elif damage == 'wrong-version':
        track['recording']['disambiguation'] = 'live'
    elif damage == 'missing-count':
        medium.pop('track-count')
    elif damage == 'truncated':
        medium['track-count'] = 2
    elif damage == 'missing-recording':
        track.pop('recording')
    elif damage == 'wrong-position':
        track['position'] = 2
    elif damage == 'wrong-media-count':
        full['media-count'] = 2
    elif damage == 'wrong-release-id':
        full['id'] = uid(99)
    elif damage == 'wrong-group':
        full['release-group']['id'] = uid(99)
    elif damage == 'wrong-release-credit':
        full['artist-credit'] = credit('Other Performer')
    elif damage == 'wrong-status':
        full['status'] = 'Bootleg'
    elif damage == 'wrong-primary-type':
        full['release-group']['primary-type'] = 'Single'
    elif damage == 'extra-live-type':
        full['release-group']['secondary-types'].append('Live')
    elif damage == 'missing-compilation-type':
        full['release-group']['secondary-types'] = []
    elif damage == 'track-title':
        track['title'] = 'Another Song'
    elif damage == 'track-credit':
        track['artist-credit'] = credit('Other Performer')
    else:
        full['date'] = '2010'
    network.documents[uid(10)] = full
    result = network.resolve(tmp_path)
    assert result['status'] != 'matched' and 'path' not in result
    assert not cover_calls(network)


@pytest.mark.parametrize('date', ['', '1997-13', '1997-02-30', 'unknown'])
def test_malformed_date_cannot_establish_compilation_preference(tmp_path, date):
    network = CompilationNetwork([compilation(date=date)])
    assert network.resolve(tmp_path)['status'] != 'matched'
    assert not cover_calls(network)


@pytest.mark.parametrize('identifier', [uid(2), uid(99)])
def test_pinned_recording_evidence_prevents_compilation_fallback(tmp_path, identifier):
    network = CompilationNetwork()
    result = network.resolve(tmp_path, musicbrainzRecordingId=identifier)
    assert result['status'] != 'matched'
    assert 'compilationArtwork' not in result['provenance']
    assert not cover_calls(network)


@pytest.mark.parametrize('problem', ['missing', 'invalid'])
def test_later_compilation_can_supply_image_after_earlier_image_miss(tmp_path, problem):
    network = CompilationNetwork([compilation(), compilation(12, 13, 'Later Collection', '2003')])
    (network.missing_covers if problem == 'missing' else network.invalid_images).add(uid(11))
    result = network.resolve(tmp_path)
    assert_artwork_only(result, 'Later Collection', 2003)
    assert result['provenance']['compilationArtwork']['trackEvidence']['releaseId'] == uid(12)
    assert len(network.calls) <= artwork.MAX_REQUESTS
    assert network.clock() - 1000 < artwork.MAX_SECONDS


def test_later_image_cannot_bypass_first_candidates_incomplete_track_evidence(tmp_path):
    network = CompilationNetwork([compilation(), compilation(12, 13, 'Later Collection', '2003')])
    full = network.full_release(uid(10))
    full['media'][0].pop('track-count')
    network.documents[uid(10)] = full
    assert network.resolve(tmp_path)['status'] != 'matched'
    assert not cover_calls(network)


def test_invalid_image_url_is_not_refetched_across_candidates(tmp_path):
    network = CompilationNetwork([compilation(), compilation(12, 13, 'Later Collection', '2003')])
    duplicate = 'https://archive.org/download/shared/broken.png'
    def answer(url):
        if url == duplicate:
            return response(raw=b'invalid', headers={'Content-Type': 'image/png'})
        if urlsplit(url).hostname == 'coverartarchive.org':
            first = uid(11) in url or uid(10) in url
            images = [{'id': 'duplicate', 'front': True, 'approved': True, 'image': duplicate}]
            if not first:
                images.append({'id': 'valid', 'front': True, 'approved': True,
                               'image': 'https://archive.org/download/second/front.png'})
            return response({'release': 'https://musicbrainz.org/release/' + uid(10 if first else 12),
                             'images': images})
    network.fault = answer
    result = network.resolve(tmp_path)
    assert_artwork_only(result, 'Later Collection', 2003)
    assert sum(c['url'] == duplicate for c in network.calls) == 1


def test_multiple_same_artist_recordings_do_not_claim_unique_audio_identity(tmp_path):
    network = CompilationNetwork()
    network.records.append(recording(90))
    network.releases[uid(90)] = copy.deepcopy(network.releases[uid(2)])
    result = network.resolve(tmp_path)
    assert_artwork_only(result)
    proof = result['provenance']['compilationArtwork']
    assert proof['recordingIds'] == [uid(2), uid(90)]
    assert proof['audioRecordingVerified'] is False


def test_embedded_track_samples_do_not_conflict_for_same_release_identity(tmp_path):
    network = CompilationNetwork()
    network.records.append(recording(90))
    network.releases[uid(90)] = copy.deepcopy(network.releases[uid(2)])
    for record in network.records:
        embedded = copy.deepcopy(network.releases[record['id']][0])
        embedded['media'] = [{'position': 1, 'tracks': [{'position': 1,
                               'recording': recording(int(record['id'][-12:]))}]}]
        record['releases'] = [embedded]
    result = network.resolve(tmp_path)
    assert_artwork_only(result)
    assert result['provenance']['compilationArtwork']['recordingIds'] == [uid(2), uid(90)]
    assert len(recording_searches(network)) == 1


@pytest.mark.parametrize('track_title', ['Example Song', 'Example Song (Acoustic)'])
def test_exact_version_compilation_survives_original_recording_miss(tmp_path, track_title):
    network = CompilationNetwork()
    network.records[0]['disambiguation'] = 'acoustic'
    full = network.full_release(uid(10))
    full['media'][0]['tracks'][0]['title'] = track_title
    network.documents[uid(10)] = full
    result = network.resolve(tmp_path, title='Example Song (Acoustic)')
    assert_artwork_only(result)
    assert result['provenance']['compilationArtwork']['versions'] == ['acoustic']


def test_specific_compilation_precedes_earlier_original_song_compilation(tmp_path):
    network = CompilationNetwork()
    network.records.append(recording(9, disambiguation='acoustic'))
    network.releases[uid(9)] = [compilation(12, 13, 'Acoustic Collection', '2020')]
    result = network.resolve(tmp_path, title='Example Song (Acoustic)')
    assert result['reason'] == 'compilation_album_cover'
    assert not {'album', 'year', 'albumStatus'} & result.keys()
    proof = result['provenance']['compilationArtwork']
    assert proof['album'] == 'Acoustic Collection'
    assert proof['trackEvidence']['recordingId'] == uid(9)
    assert proof['lookupContext'] == 'source-version'
    assert not any(uid(11) in c['url'] for c in cover_calls(network))


@pytest.mark.parametrize('damage', ['date', 'identity'])
def test_valid_specific_compilation_does_not_depend_on_weaker_original_compilation(tmp_path, damage):
    network = CompilationNetwork()
    network.records.append(recording(9, disambiguation='acoustic'))
    network.releases[uid(9)] = [compilation(12, 13, 'Acoustic Collection', '2020')]
    original = network.releases[uid(2)][0]
    if damage == 'date':
        original['date'] = original['release-group']['first-release-date'] = '1997-13'
    else:
        network.group_documents[uid(11)] = {**original['release-group'], 'artist-credit': credit('Other Performer')}
    result = network.resolve(tmp_path, title='Example Song (Acoustic)')
    assert result['reason'] == 'compilation_album_cover'
    assert result['provenance']['compilationArtwork']['album'] == 'Acoustic Collection'
    assert not any('/ws/2/release-group/' + uid(11) in c['url'] for c in network.calls)


def test_original_song_album_precedes_exact_version_compilation(tmp_path):
    network = CompilationNetwork([release()])
    network.records.append(recording(9, disambiguation='acoustic'))
    network.releases[uid(9)] = [compilation()]
    result = network.resolve(tmp_path, title='Example Song (Acoustic)')
    assert result['reason'] == 'original_song_album_cover'
    assert result['provenance']['originalSongArtwork']['album'] == 'Original Album'
    assert not any(uid(11) in c['url'] for c in cover_calls(network))


@pytest.mark.parametrize('reverse', [False, True])
def test_artwork_tie_break_is_stable_for_equal_dates_and_duplicate_titles(tmp_path, reverse):
    candidates = [compilation(10, 11, 'Artist Collection', '1997'), compilation(12, 13, 'Artist Collection', '1997')]
    network = CompilationNetwork(list(reversed(candidates)) if reverse else candidates)
    result = network.resolve(tmp_path)
    assert_artwork_only(result)
    assert result['provenance']['compilationArtwork']['releaseGroupId'] == uid(11)
    assert not any(uid(13) in c['url'] for c in cover_calls(network))


def test_candidate_cap_limits_attempts_without_exhaustive_absence_claim(tmp_path):
    from feedback_converter.song_import.compilation_artwork import MAX_COMPILATION_CANDIDATES
    candidates = [compilation(20 + i * 2, 21 + i * 2, f'Collection {i}', str(1990 + i))
                  for i in range(MAX_COMPILATION_CANDIDATES + 1)]
    network = CompilationNetwork(candidates)
    network.missing_covers = {r['release-group']['id'] for r in candidates[:-1]}
    result = network.resolve(tmp_path)
    assert result['status'] != 'matched'
    assert not any(candidates[-1]['release-group']['id'] in c['url'] for c in cover_calls(network))
    proof = result['provenance']['compilationArtworkAttempt']
    assert proof['candidateCount'] == MAX_COMPILATION_CANDIDATES + 1
    assert proof['candidateLimit'] == MAX_COMPILATION_CANDIDATES
    assert proof['candidateLimitReached'] is True
    assert len(proof['candidateChecks']) == MAX_COMPILATION_CANDIDATES
    assert len(network.calls) <= artwork.MAX_REQUESTS


def test_excessive_group_nominations_cannot_choose_arbitrary_subset(tmp_path):
    from feedback_converter.song_import.compilation_artwork import MAX_COMPILATION_GROUPS
    candidates = [compilation(20 + i * 2, 21 + i * 2, f'Collection {i}', str(1990 + i))
                  for i in range(MAX_COMPILATION_GROUPS + 1)]
    network = CompilationNetwork(candidates)
    result = network.resolve(tmp_path)
    assert result['status'] != 'matched'
    assert not cover_calls(network)
    assert len(network.calls) <= artwork.MAX_REQUESTS


def test_request_limit_is_shared_with_preferred_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(artwork, 'MAX_REQUESTS', 3)
    network = CompilationNetwork()
    result = network.resolve(tmp_path, title='Example Song (Acoustic)')
    assert result['status'] != 'matched'
    assert len(network.calls) == 3
    assert 'lookup_budget_exceeded' in json.dumps(result)
    assert not cover_calls(network)


def test_elapsed_time_limit_is_shared_with_compilation_fallback(tmp_path):
    network = CompilationNetwork()
    def slow(url):
        network.clock.value += 16
    network.fault = slow
    result = network.resolve(tmp_path)
    assert result['status'] != 'matched'
    assert 'lookup_budget_exceeded' in json.dumps(result)
    assert all(call['time'] - 1000 < artwork.MAX_SECONDS for call in network.calls)
    assert not cover_calls(network)


@pytest.mark.parametrize('reason', ['service_unavailable', 'incomplete_catalogue'])
def test_catalogue_failure_does_not_open_compilation_fallback(tmp_path, reason):
    network = CompilationNetwork()
    network.fault = lambda url: (response(status=503) if reason == 'service_unavailable' else
                                 response({'count': 2, 'recordings': network.records}))
    result = network.resolve(tmp_path)
    assert result['reason'] == reason
    assert 'compilationArtworkAttempt' not in result['provenance']
    assert not cover_calls(network)


@pytest.mark.parametrize('reason', ['artist_ambiguous', 'incomplete_catalogue',
                                  'service_unavailable', 'lookup_budget_exceeded'])
def test_unsafe_original_song_retry_cannot_be_hidden_by_specific_semantic_miss(tmp_path, monkeypatch, reason):
    from feedback_converter.song_import import artwork_fallback
    original_song_cover = artwork_fallback.original_song_cover
    def retry(client, values, specific, cache, now):
        assert specific['reason'] == 'recording_not_found'
        def denied(*args, **kwargs):
            raise artwork._LookupFailure(reason)
        with monkeypatch.context() as patch:
            patch.setattr(artwork_fallback, 'select_album', denied)
            return original_song_cover(client, values, specific, cache, now)
    monkeypatch.setattr(artwork_fallback, 'original_song_cover', retry)
    network = CompilationNetwork()
    result = network.resolve(tmp_path, title='Example Song (Acoustic)')
    assert result['status'] != 'matched'
    assert result['provenance']['originalSongArtworkAttempt']['reason'] == reason
    assert 'compilationArtworkAttempt' not in result['provenance']
    assert not cover_calls(network)


def seed_old_result(cache, network, reason, *, title='Example Song', provenance=None):
    values = {'artist': 'Example Artist', 'title': title, 'album': '', 'audioTitle': ''}
    key = hashlib.sha256(json.dumps([artwork.POLICY_VERSION, values], ensure_ascii=False,
                                    sort_keys=True).encode()).hexdigest()
    cache.mkdir()
    saved = {'version': artwork.POLICY_VERSION, 'createdAt': network.clock(), 'ttl': 86400,
             'recordingPaginationVersion': 1, 'albumCandidateVersion': 1, 'officialReleaseDateVersion': 1,
             'result': {'status': 'unavailable', 'reason': reason, 'provenance': provenance or {}}}
    (cache / f'{key}.json').write_text(json.dumps(saved), encoding='utf-8')


def test_previous_policy_eligible_album_miss_retries_once(tmp_path):
    network = CompilationNetwork()
    cache = tmp_path / 'cache'
    seed_old_result(cache, network, 'album_not_found')
    assert_artwork_only(network.resolve(tmp_path / 'fresh', cache=cache))
    calls = len(network.calls)
    assert_artwork_only(network.resolve(tmp_path / 'warm', cache=cache))
    assert len(network.calls) == calls


def test_previous_policy_original_song_album_miss_retries_without_broad_recording_invalidation(tmp_path):
    network = CompilationNetwork()
    cache = tmp_path / 'cache'
    title = 'Example Song (Acoustic)'
    seed_old_result(cache, network, 'recording_not_found', title=title,
                    provenance={'originalSongArtworkAttempt': {'status': 'unavailable', 'reason': 'album_not_found'}})
    assert_artwork_only(network.resolve(tmp_path / 'fresh', cache=cache, title=title))
    assert network.calls


@pytest.mark.parametrize('reason', ['recording_not_found', 'artist_ambiguous', 'album_ambiguous',
                                  'incomplete_catalogue', 'lookup_budget_exceeded', 'service_unavailable'])
def test_previous_policy_unrelated_negative_cache_remains_reusable(tmp_path, reason):
    network = CompilationNetwork()
    cache = tmp_path / 'cache'
    seed_old_result(cache, network, reason)
    result = network.resolve(tmp_path / 'result', cache=cache)
    assert result['reason'] == reason
    assert not network.calls


def test_previous_policy_successful_cover_remains_reusable(tmp_path):
    network = CompilationNetwork([release()])
    cache = tmp_path / 'cache'
    first = network.resolve(tmp_path / 'first', cache=cache)
    assert first['reason'] == 'album_cover'
    for record in cache.glob('*.json'):
        saved = json.loads(record.read_text(encoding='utf-8'))
        saved.pop('compilationArtworkVersion', None)
        record.write_text(json.dumps(saved), encoding='utf-8')
    calls = len(network.calls)
    second = network.resolve(tmp_path / 'second', cache=cache)
    assert second['reason'] == 'album_cover' and second['album'] == first['album']
    assert len(network.calls) == calls


def seed_cached_release(cache, network, document):
    url = f'https://musicbrainz.org/ws/2/release/{uid(10)}?fmt=json&inc=recordings+artist-credits+release-groups'
    folder = cache / 'lookups'
    folder.mkdir(parents=True)
    path = folder / (hashlib.sha256(url.encode()).hexdigest() + '.json')
    path.write_text(json.dumps({'version': artwork.POLICY_VERSION, 'createdAt': network.clock(),
                                'data': document}), encoding='utf-8')
    return url, path


@pytest.mark.parametrize('stale', [None, 'missing-current-track', 'incomplete', 'wrong-group'])
def test_stale_shared_release_is_refreshed_once_before_using_current_track(tmp_path, stale):
    network = CompilationNetwork()
    cache = tmp_path / 'cache'
    full = network.full_release(uid(10))
    if stale == 'missing-current-track':
        full['media'][0]['tracks'][0]['recording'] = recording(99, title='Another Song')
    elif stale == 'incomplete':
        full['media'][0].pop('track-count')
    elif stale == 'wrong-group':
        full['release-group']['id'] = uid(99)
    if stale:
        url, cached = seed_cached_release(cache, network, full)
    else:
        url = f'https://musicbrainz.org/ws/2/release/{uid(10)}?fmt=json&inc=recordings+artist-credits+release-groups'
    result = network.resolve(tmp_path / 'result', cache=cache)
    assert_artwork_only(result)
    assert sum(call['url'] == url for call in network.calls) == 1
    proof = result['provenance']['compilationArtwork']['trackEvidence']
    assert proof['releaseId'] == uid(10) and proof['recordingId'] == uid(2)
    if stale:
        assert json.loads(cached.read_text(encoding='utf-8'))['data'] == network.full_release(uid(10))
    assert len(network.calls) <= artwork.MAX_REQUESTS
    assert network.clock() - 1000 < artwork.MAX_SECONDS


@pytest.mark.parametrize('fresh_problem', ['artist', 'title', 'version', 'incomplete', 'missing-track'])
def test_stale_release_refresh_cannot_bypass_fresh_identity_or_track_failure(tmp_path, fresh_problem):
    network = CompilationNetwork([compilation(), compilation(12, 13, 'Later Collection', '2003')])
    cache = tmp_path / 'cache'
    stale = network.full_release(uid(10))
    stale['media'][0]['tracks'][0]['recording'] = recording(99, title='Another Song')
    url, _ = seed_cached_release(cache, network, stale)
    full = network.full_release(uid(10))
    record = full['media'][0]['tracks'][0]['recording']
    if fresh_problem == 'artist':
        record['artist-credit'] = credit('Other Performer')
    elif fresh_problem == 'title':
        record['title'] = 'Another Song'
    elif fresh_problem == 'version':
        record['disambiguation'] = 'live'
    elif fresh_problem == 'incomplete':
        full['media'][0].pop('track-count')
    else:
        full['media'][0]['tracks'][0]['recording'] = recording(99, title='Another Song')
    network.documents[uid(10)] = full
    result = network.resolve(tmp_path / 'result', cache=cache)
    assert result['status'] != 'matched' and 'path' not in result
    assert sum(call['url'] == url for call in network.calls) == 1
    assert not cover_calls(network), 'a fresh contradiction cannot advance to a later image'
    assert len(network.calls) <= artwork.MAX_REQUESTS


def test_stale_release_refresh_consumes_the_existing_request_budget(tmp_path, monkeypatch):
    network = CompilationNetwork()
    cache = tmp_path / 'cache'
    stale = network.full_release(uid(10))
    stale['media'][0].pop('track-count')
    url, _ = seed_cached_release(cache, network, stale)
    monkeypatch.setattr(artwork, 'MAX_REQUESTS', 3)
    result = network.resolve(tmp_path / 'result', cache=cache)
    assert result['reason'] == 'lookup_budget_exceeded'
    assert len(network.calls) == 3
    assert not any(call['url'] == url for call in network.calls)
    assert not cover_calls(network)


@pytest.mark.parametrize('source_album', [None, 'Source version album'])
@pytest.mark.parametrize('warm', [False, True])
def test_feedpak_uses_cover_without_compilation_album_year_or_chart_changes(tmp_path, source_album, warm):
    from feedback_converter.song_import.builder import build_feedpak
    from test_song_import_builder import inputs
    performance, audio, alignment, job = inputs(tmp_path)
    performance.update(artist='Example Artist', title='Example Song')
    if source_album:
        performance.update(album=source_album, year=2025)
    before = copy.deepcopy(performance)
    network = CompilationNetwork()
    options = {'cache': tmp_path / 'cache', 'album': source_album or ''}
    art = network.resolve(tmp_path / 'cold', **options)
    if warm:
        calls = len(network.calls)
        art = network.resolve(tmp_path / 'warm', **options)
        assert len(network.calls) == calls
    assert_artwork_only(art)
    result = build_feedpak(performance, audio, alignment, job, output_dir=tmp_path / 'out', artwork=art)
    assert performance == before
    with zipfile.ZipFile(result['stagingPath']) as archive:
        manifest = yaml.safe_load(archive.read('manifest.yaml'))
        assert (manifest['artist'], manifest['title']) == ('Example Artist', 'Example Song')
        assert manifest.get('album') == source_album
        assert manifest.get('year') == (2025 if source_album else None)
        assert archive.read(manifest['cover']) == Path(art['path']).read_bytes()
        chart = json.loads(archive.read(manifest['arrangements'][0]['file']))
        assert chart['notes'][0]['t'] == pytest.approx(1.85)
        assert chart['notes'][0]['sus'] == pytest.approx(1.1)


@pytest.mark.parametrize('warm', [False, True])
def test_worker_and_recipe_keep_compilation_scope_without_metadata_pollution(import_job, monkeypatch, tmp_path, warm):
    request, performance, audio, alignment, _, built = import_job
    network = CompilationNetwork()
    real_resolve = artwork.resolve_album_art
    def lookup(metadata, directory, **kwargs):
        return real_resolve(metadata, directory, **kwargs, transport=network, clock=network.clock,
                            sleep=network.clock.sleep, now=network.clock)
    monkeypatch.setattr(artwork, 'resolve_album_art', lookup)
    if warm:
        lookup({**request['metadata'], 'album': None, 'audioTitle': audio['source']['title']},
               tmp_path / 'prime', cache_dir=Path(request['artworkCacheDir']))
    calls = len(network.calls)
    original_audio = copy.deepcopy(audio)
    original_alignment = copy.deepcopy(alignment)
    result = worker.run_import(request)
    assert result['ok'], result
    for container in (result['artwork'], result['recipe']['artwork'], built[0]['performance']):
        assert not {'album', 'year', 'albumStatus'} & container.keys()
    assert result['artwork']['reason'] == 'compilation_album_cover'
    assert result['recipe']['artwork']['provenance']['matchingScope'] == 'compilation-artwork'
    assert built[0]['recipe']['artwork'] == result['recipe']['artwork']
    assert built[0]['cover']
    assert audio == original_audio
    assert built[0]['alignment']['provenance'] == original_alignment['provenance']
    assert not {'album', 'year'} & performance.keys()
    if warm:
        assert len(network.calls) == calls
