"""Verified same-artist compilations may supply art, never album metadata."""
from .artwork import (_EXCLUDE, _LookupFailure, _UUID, _album_cover, _matched_album_cover,
                      _norm, _result, _title_identity, POLICY_VERSION)
from .album_match import (_complete_tracklist, _group_nominations, _ids, _interval,
                          _matches, _recording_releases, _recordings, select_album,
                          MAX_GROUPS, MAX_TRACK_EDITIONS)
from .artwork_fallback import original_song_result, original_song_values

MAX_COMPILATION_GROUPS = MAX_GROUPS
MAX_COMPILATION_CANDIDATES = 3
_MISSES = {'recording_not_found', 'album_not_found', 'cover_not_available',
           'invalid_cover_image', 'response_too_large'}
_IMAGE_MISSES = {'cover_not_available', 'invalid_cover_image', 'response_too_large'}
_VARIOUS_ARTISTS = '89ad4ac3-39f7-470e-963a-56509c546377'


def compilation_miss_eligible(result):
    """An unsafe original-song retry cannot be hidden by its earlier miss."""
    if result.get('status') == 'matched' or result.get('reason') not in _MISSES:
        return False
    original = result.get('provenance', {}).get('originalSongArtworkAttempt')
    return original is None or (isinstance(original, dict) and original.get('reason') in _MISSES)


def compilation_cache_miss_eligible(result):
    if not compilation_miss_eligible(result):
        return False
    # A bare missing recording contains no validated identity to reconsider.
    # Version lookups can instead have an eligible original-song album miss.
    return (result.get('reason') != 'recording_not_found'
            or result.get('provenance', {}).get('originalSongArtworkAttempt', {}).get('reason')
            in _MISSES - {'recording_not_found'})


def _eligible(release, artist_ids):
    if not isinstance(release, dict):
        raise _LookupFailure('invalid_metadata')
    # A fully identified different performer cannot supply same-artist art.
    # Reject it before inspecting optional album details. Incomplete credits
    # do not establish an unrelated identity and must not bypass validation.
    credits = release.get('artist-credit')
    if (artist_ids and isinstance(credits, list) and credits
            and all(isinstance(c, dict) and isinstance(c.get('artist'), dict) for c in credits)):
        release_ids = _ids(release)
        if release_ids and release_ids != artist_ids:
            return False
    group = release.get('release-group')
    if not isinstance(group, dict):
        raise _LookupFailure('invalid_metadata')
    secondary = group.get('secondary-types')
    if not isinstance(secondary, list) or any(not isinstance(t, str) for t in secondary):
        raise _LookupFailure('invalid_metadata')
    secondary = {t.lower() for t in secondary}
    if ('compilation' not in secondary or secondary & (_EXCLUDE - {'compilation'})
            or release.get('status') != 'Official'
            or str(group.get('primary-type', '')).lower() not in {'album', 'ep'}):
        return False
    # No partial credits, Various Artists, cover performer, or extra collaborator.
    if not artist_ids or _VARIOUS_ARTISTS in artist_ids or _ids(release) != artist_ids:
        return False
    if any(_norm(c['artist'].get('name')) == 'variousartists' for c in release['artist-credit']):
        return False
    if not _UUID.fullmatch(str(group.get('id') or '')):
        raise _LookupFailure('invalid_metadata')
    return True


def _nominations(client, records):
    groups = {}
    for record in records:
        for release in _recording_releases(client, record):
            if not _eligible(release, _ids(record)):
                continue
            if not _UUID.fullmatch(str(release.get('id') or '')):
                raise _LookupFailure('invalid_metadata')
            linked = release['release-group']
            release_type = 'Album' if linked['primary-type'].lower() == 'album' else 'EP'
            group = groups.setdefault(linked['id'], {
                'id': linked['id'], 'artistIds': _ids(record), 'releaseType': release_type,
                'releases': {}, 'records': {},
            })
            if group['artistIds'] != _ids(record) or group['releaseType'] != release_type:
                raise _LookupFailure('album_identity_unconfirmed', 'ambiguous')
            previous = group['releases'].get(release['id'])
            # Embedded recording searches include per-recording track samples.
            # Compare catalogue identity, never those intentionally different
            # media snippets; full independent tracklists provide the proof.
            if previous is not None and any(previous.get(k) != release.get(k) for k in
                    ('date', 'status', 'artist-credit', 'release-group')):
                raise _LookupFailure('incomplete_catalogue', 'ambiguous')
            group['releases'][release['id']] = release
            group['records'][record['id']] = record
    return list(groups.values())


def _verify_groups(client, groups):
    if len(groups) > MAX_COMPILATION_GROUPS:
        raise _LookupFailure('album_ambiguous', 'ambiguous')
    # Verify the bounded nomination set before applying an artwork-only date/ID
    # preference. Catalogue order never decides which compilation supplies art.
    for group in sorted(groups, key=lambda g: g['id']):
        full = client.json(f"https://musicbrainz.org/ws/2/release-group/{group['id']}?fmt=json&inc=artist-credits", 'mb')
        if (not full or full.get('id') != group['id'] or _ids(full) != group['artistIds']
                or str(full.get('primary-type', '')).lower() != group['releaseType'].lower()):
            raise _LookupFailure('album_identity_unconfirmed', 'ambiguous')
        probe = {'status': 'Official', 'release-group': full, 'artist-credit': full.get('artist-credit')}
        if not _eligible(probe, group['artistIds']):
            raise _LookupFailure('album_identity_unconfirmed', 'ambiguous')
        if not isinstance(full.get('title'), str) or not full['title'].strip():
            raise _LookupFailure('invalid_metadata')
        _interval(full.get('first-release-date'))
        dated = [(_interval(r.get('date')), r['id']) for r in group['releases'].values()]
        group.update(album=full['title'], interval=min(dated)[0])
    return sorted(groups, key=lambda g: (g['interval'][0], g['interval'][1], g['id']))


def _confirm_compilation_track(client, group, metadata, title, versions):
    editions = sorted(group['releases'].values(), key=lambda r: (*_interval(r.get('date')), r['id']))
    for edition in editions[:MAX_TRACK_EDITIONS]:
        url = f"https://musicbrainz.org/ws/2/release/{edition['id']}?fmt=json&inc=recordings+artist-credits+release-groups"
        full = client.json(url, 'mb')
        if client.last_json_cached and (not full or (full.get('release-group') or {}).get('id') != group['id']
                or not _complete_tracklist(full) or not any(t['recording']['id'] in group['records']
                    for m in full['media'] for t in m['tracks'])):
            # A shared entity cache can predate the current recording search.
            # Recheck once before treating stale membership as a contradiction.
            full = client.json(url, 'mb', fresh=True)
        if (not full or full.get('id') != edition['id'] or full.get('status') != 'Official'
                or (full.get('release-group') or {}).get('id') != group['id']
                or _ids(full) != group['artistIds']
                or str((full.get('release-group') or {}).get('primary-type', '')).lower() != group['releaseType'].lower()
                or not _eligible(full, group['artistIds'])):
            raise _LookupFailure('album_identity_unconfirmed', 'ambiguous')
        if _interval(full.get('date')) != _interval(edition.get('date')):
            raise _LookupFailure('album_date_unknown', 'ambiguous')
        if not _complete_tracklist(full):
            raise _LookupFailure('album_track_unconfirmed', 'ambiguous')
        for medium in full['media']:
            for track in medium['tracks']:
                record = track['recording']
                # A linked ID does not override contradictory title, performer,
                # or version metadata in the independently fetched tracklist.
                if not _matches(record, metadata, title, versions, _title_identity(title)[2], group['artistIds']):
                    if record['id'] in group['records']:
                        raise _LookupFailure('album_identity_unconfirmed', 'ambiguous')
                    continue
                if track.get('artist-credit') is not None and _ids(track) != group['artistIds']:
                    raise _LookupFailure('album_identity_unconfirmed', 'ambiguous')
                if 'title' in track and (not isinstance(track['title'], str) or not track['title'].strip()
                        or not _matches({**record, 'title': track['title']}, metadata, title, versions,
                                        _title_identity(title)[2], group['artistIds'])):
                    raise _LookupFailure('album_identity_unconfirmed', 'ambiguous')
                group['release'] = full
                group['trackEvidence'] = {
                    'releaseId': full['id'], 'recordingId': record['id'],
                    'medium': medium['position'], 'track': track['position'],
                    'basis': 'verified-compilation-release-track',
                    'recordingMatch': 'linked-id-and-exact-artist-title-version' if record['id'] in group['records'] else 'exact-artist-title-version',
                }
                return
    # Bounded editions are insufficient evidence for skipping an uncertain
    # candidate and silently choosing a different identity through its image.
    raise _LookupFailure('album_track_unconfirmed', 'ambiguous')


def _ordinary_ids(client, records, metadata, versions):
    return {(g['id'], r) for g in _group_nominations(client, records, metadata, versions) for r in g['releases']}


def _ordered_candidates(client, prepared):
    nominations = [(context, _nominations(client, context[2])) for context in prepared]
    count = sum(len(groups) for _, groups in nominations)
    rank, verified_count, order = 0, 0, []
    for context, groups in nominations:
        if rank >= MAX_COMPILATION_CANDIDATES:
            return
        # The group and image-candidate caps apply across both contexts. Do not
        # spend requests verifying weaker original-song candidates before an
        # exact-version compilation has had its opportunity to supply artwork.
        if verified_count + len(groups) > MAX_COMPILATION_GROUPS:
            raise _LookupFailure('album_ambiguous', 'ambiguous')
        groups = _verify_groups(client, groups)
        verified_count += len(groups)
        order.extend(g['id'] for g in groups)
        for group in groups:
            if rank >= MAX_COMPILATION_CANDIDATES:
                return
            rank += 1
            yield group, context, {
                'candidateOrder': list(order), 'candidateOrderScope': 'verified-contexts',
                'candidateRank': rank, 'candidateCount': count,
                'candidateLimit': MAX_COMPILATION_CANDIDATES,
                'candidateLimitReached': rank >= MAX_COMPILATION_CANDIDATES and count > rank,
                'groupLimit': MAX_COMPILATION_GROUPS, 'verifiedGroupCount': verified_count,
            }


def compilation_cover(client, values, specific, cache, now):
    if values.get('musicbrainzRecordingId') or not compilation_miss_eligible(specific):
        return specific, None
    original_attempt = specific.get('provenance', {}).get('originalSongArtworkAttempt')
    checks = []
    try:
        contexts = [(values, False)]
        original = original_song_values(values) if original_attempt else None
        if original is not None:
            contexts.append((original, True))
        prepared, seen = [], set()
        # Complete/recover both preferred ordinary paths before selecting art.
        # Exact source versions then precede relaxed original-song compilations.
        for metadata, relaxed in contexts:
            records, title, versions, _, credit_resolution, video_resolution = _recordings(client, metadata)
            if not records or not _nominations(client, records):
                continue
            previous = _ordinary_ids(client, records, metadata, versions)
            for record in sorted(records, key=lambda r: r['id']):
                _recording_releases(client, record, complete=True)
            if _ordinary_ids(client, records, metadata, versions) - previous:
                group = select_album(client, metadata)
                preferred, raw = _matched_album_cover(client, group, metadata, cache, now)
                if raw is not None:
                    return (original_song_result(metadata, specific, preferred) if relaxed else preferred), raw
                if not compilation_miss_eligible(preferred):
                    raise _LookupFailure(preferred['reason'], preferred['status'])
                if relaxed:
                    specific = {**specific, 'provenance': {**specific['provenance'],
                                'originalSongArtworkAttempt': {'status': preferred['status'], 'reason': preferred['reason']}}}
                else:
                    specific = preferred
            identity = (title, tuple(sorted(versions)), tuple(sorted(r['id'] for r in records)))
            if identity not in seen:
                seen.add(identity)
                prepared.append((metadata, relaxed, records, title, versions, credit_resolution, video_resolution))
        attempted_images = set()
        ranking = {}
        for group, context, ranking in _ordered_candidates(client, prepared):
            metadata, relaxed, _, title, versions, credit_resolution, video_resolution = context
            _confirm_compilation_track(client, group, metadata, title, versions)
            check = {'releaseGroupId': group['id'], 'releaseId': group['release']['id'],
                     'trackEvidence': group['trackEvidence']}
            checks.append(check)
            group['_attemptedCoverImages'] = attempted_images
            try:
                raw, image_provenance = _album_cover(client, group, cache, now)
            except _LookupFailure as exc:
                check.update(status=exc.status, reason=exc.reason)
                if exc.reason not in _IMAGE_MISSES:
                    raise
                continue
            check.update(status='matched', reason='compilation_album_cover')
            proof = {
                'artist': metadata['artist'], 'artistIds': list(group['artistIds']),
                'album': group['album'], 'year': _interval(group['release']['date'])[0].year,
                'releaseGroupId': group['id'], 'releaseId': group['release']['id'],
                'releaseType': group['releaseType'], 'secondaryTypes': ['Compilation'],
                'recordingIds': sorted(group['records']), 'lookupTitle': title, 'versions': sorted(versions),
                'trackEvidence': group['trackEvidence'],
                'selection': 'source-version-then-earliest-dated-linked-official-compilation-with-artwork',
                'lookupContext': 'original-song' if relaxed else 'source-version',
                **ranking, 'candidateChecks': checks,
                'audioRecordingVerified': False, **image_provenance,
            }
            if credit_resolution:
                proof['artistCreditResolution'] = credit_resolution['proof']
            if video_resolution:
                proof['artistVideoResolution'] = video_resolution
            provenance = {**image_provenance, 'policyVersion': POLICY_VERSION,
                          'matchingScope': 'compilation-artwork', 'audioRecordingVerified': False,
                          'compilationArtwork': proof,
                          'specificArtwork': {'status': specific['status'], 'reason': specific['reason'],
                                              'provenance': specific['provenance']}}
            enrichment = {k: specific[k] for k in ('albumStatus', 'album', 'year') if k in specific}
            result = _result('matched', 'compilation_album_cover', provenance=provenance, **enrichment)
            result['message'] = f"Using compilation artwork from {group['album']}; artwork for this version was not found."
            return result, raw
        if checks:
            return {**specific, 'provenance': {**specific['provenance'], 'compilationArtworkAttempt': {
                'status': 'unavailable', 'reason': checks[-1]['reason'], 'candidateChecks': checks,
                **ranking}}}, None
        return specific, None
    except _LookupFailure as exc:
        # Preserve the specific miss and any independently proven album, but
        # expose new identity/incomplete/budget failures as the final outcome.
        enrichment = {k: specific[k] for k in ('albumStatus', 'album', 'year') if k in specific}
        provenance = {**specific['provenance'], 'compilationArtworkAttempt': {
            'status': exc.status, 'reason': exc.reason, 'candidateChecks': checks},
            'specificArtwork': {'status': specific['status'], 'reason': specific['reason'],
                                'provenance': specific['provenance']}}
        return _result(exc.status, exc.reason, provenance=provenance, **enrichment), None
