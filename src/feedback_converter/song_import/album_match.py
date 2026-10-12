"""Bounded album-level matching; never claims to identify an audio master."""
from calendar import monthrange
from datetime import date
import re

from .artwork import (_LookupFailure, _norm, _artist_names, _title_identity,
                      _search_title, _query_url, _pages, _UUID, _EXCLUDE, _VERSIONS)

MAX_RECORDINGS = 300
MAX_GROUPS = 8
MAX_RELEASES = 300
MAX_TRACK_EDITIONS = 3


class _CheckedTrackMismatch(_LookupFailure):
    """No matching track in valid checked editions, not universal absence."""

    def __init__(self, evidence):
        super().__init__('album_track_unconfirmed', 'ambiguous')
        self.evidence = evidence


def _quote(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"') + '"'


def _ids(record):
    credits = record.get('artist-credit', [])
    if not isinstance(credits, list) or not credits:
        return ()
    result = tuple(c.get('artist', {}).get('id') for c in credits if isinstance(c, dict))
    return result if len(result) == len(credits) and all(_UUID.fullmatch(str(i or '')) for i in result) else ()


def _interval(text):
    if not re.fullmatch(r'\d{4}(?:-\d{2}){0,2}', str(text or '')):
        raise _LookupFailure('album_date_unknown', 'ambiguous')
    try:
        parts = list(map(int, text.split('-')))
        year, month, day = parts[0], parts[1] if len(parts) > 1 else None, parts[2] if len(parts) > 2 else None
        return date(year, month or 1, day or 1), date(year, month or 12, day or (monthrange(year, month)[1] if month else 31))
    except ValueError:
        raise _LookupFailure('album_date_unknown', 'ambiguous') from None


def _contains(text, name):
    # Match whole identities, not e.g. "One" inside "Stone".
    words = re.findall(r'\w+', str(name), flags=re.U)
    pattern = r'[\W_]*'.join(re.escape(w) for w in words)
    return bool(pattern and re.search(r'(?<!\w)' + pattern + r'(?!\w)', str(text), re.I))


def _chart_base(title):
    return re.sub(r'\s+(?:\(?guitar solo\)?|\(?solo\)?|\(?guitar tab\)?|\(?bass tab\)?)$', '', title, flags=re.I)


def _audio_context(metadata, names, title):
    audio = metadata.get('audioTitle', '')
    if not audio or not any(_contains(audio, name) for name in names) or not _contains(audio, title):
        return ''
    return audio


def _lookup_title(metadata, names):
    title = metadata['title']
    # A chart suffix may only be removed when the selected recording corroborates
    # the base title and does not itself carry that suffix. Never change metadata.
    base = _chart_base(title)
    audio = _audio_context(metadata, names, base)
    return base if base != title and audio and not _contains(audio, title) else title


def _context_versions(title, audio, names):
    _, versions, details = _title_identity(title)
    if audio:
        remainder = audio
        for name in sorted([title, *names], key=len, reverse=True):
            pattern = r'[\W_]*'.join(re.escape(c) for c in name if c.isalnum())
            if pattern:
                remainder = re.sub(pattern, '', remainder, flags=re.I)
        # Context is optional corroboration. Studio/official-video boilerplate
        # adds no version. An explicit live session must not get a studio cover.
        if re.search(r'\b(?:live|unplugged)\b', remainder, re.I):
            versions.add('live')
        for tag in ('acoustic', 'remix', 'demo', 'instrumental'):
            if re.search(r'\b' + tag + r'\b', remainder, re.I):
                versions.add(tag)
        if re.search(_VERSIONS['djmix'], remainder, re.I):
            versions.add('djmix')
    return versions, details


def _search(client, title, artist, versions, artist_ids=()):
    who = ' AND '.join('arid:' + i for i in artist_ids) if artist_ids else 'artist:' + _quote(artist)
    query = 'recording:' + _quote(_search_title(title)) + ' AND ' + who
    query += ' AND status:official AND primarytype:(album OR ep) AND -video:true'
    for tag in ('live', 'demo', 'remix'):
        if tag not in versions:
            query += ' AND -comment:' + tag
    return _pages(client, 'recording', {'query': query}, 'recordings', 'count', MAX_RECORDINGS,
                  recover_recording_overlap=True)


def _matches(record, metadata, title, versions, details, artist_ids=()):
    if record.get('video') is True or not _ids(record):
        return False
    canonical = [c['artist'].get('name', '') for c in record['artist-credit']]
    if any(str(name).startswith('[') for name in canonical):
        return False
    if artist_ids:
        if _ids(record) != artist_ids:
            return False
    elif _norm(metadata['artist']) not in _artist_names(record):
        return False
    base, tags, candidate_details = _title_identity(record.get('title', ''), record.get('disambiguation', ''))
    target = _title_identity(title)[0]
    # Acoustic live performances are often labelled just "live" in the
    # catalogue. Do not infer this exception for a studio acoustic version.
    # Mono/stereo format alone does not change album membership. An explicit
    # source format still constrains the match, as do live/remix/remaster tags.
    compared_tags = tags if versions & {'mono', 'stereo'} else tags - {'mono', 'stereo'}
    compatible = compared_tags == versions or ('live' in versions and compared_tags == versions - {'acoustic'})
    specific = {d for d in details if d not in {_norm(t) for t in versions}}
    return base == target and compatible and (not specific or specific.issubset(candidate_details))


def _artist_lookup(client, artist):
    records = _pages(client, 'artist', {'query': 'artist:' + _quote(artist)}, 'artists', 'count', 100)
    matches = []
    for record in records:
        names = [record.get('name', ''), *[a.get('name', '') for a in record.get('aliases', []) if isinstance(a, dict)]]
        if _norm(artist) in {_norm(n) for n in names}:
            matches.append(record)
    if len(matches) != 1:
        raise _LookupFailure('artist_ambiguous' if matches else 'recording_not_found', 'ambiguous' if matches else 'unavailable')
    candidate = client.json(f"https://musicbrainz.org/ws/2/artist/{matches[0]['id']}?fmt=json&inc=aliases", 'mb')
    if not candidate or candidate.get('id') != matches[0]['id']:
        raise _LookupFailure('artist_ambiguous', 'ambiguous')
    names = [candidate.get('name', ''), *[a.get('name', '') for a in candidate.get('aliases', []) if isinstance(a, dict)]]
    if _norm(artist) not in {_norm(n) for n in names}:
        raise _LookupFailure('artist_ambiguous', 'ambiguous')
    return (candidate['id'],), names


def _recordings(client, metadata):
    credit_resolution = None
    names = [metadata['artist']]
    title = _lookup_title(metadata, names)
    audio = _audio_context(metadata, names, _search_title(title))
    versions, details = _context_versions(title, audio, names)
    identifier = metadata.get('musicbrainzRecordingId')
    if identifier:
        record = client.json(f'https://musicbrainz.org/ws/2/recording/{identifier}?fmt=json&inc=artist-credits+releases+release-groups', 'mb')
        records = [record] if record and record.get('id') == identifier else []
    else:
        records = _search(client, title, metadata['artist'], versions)
        base = _chart_base(title)
        if not records and base != title and _contains(metadata.get('audioTitle', ''), base):
            # Probe the base title only to discover a credited artist identity.
            # Acceptance still requires the selected audio to corroborate both
            # that verified alias and the base song title.
            probed = _search(client, base, metadata['artist'], versions)
            for record in probed:
                if _matches(record, metadata, base, versions, details):
                    names += [c['artist']['name'] for c in record['artist-credit']]
            if _lookup_title(metadata, names) == base:
                title, records = base, probed
                audio = _audio_context(metadata, names, _search_title(title))
                versions, details = _context_versions(title, audio, names)
    matches = [r for r in records if _matches(r, metadata, title, versions, details)]
    identities = {_ids(r) for r in matches}
    if len(identities) > 1:
        raise _LookupFailure('artist_ambiguous', 'ambiguous')
    artist_ids = next(iter(identities), ())
    if matches:
        names += [c['artist']['name'] for r in matches for c in r['artist-credit']]
    # A credited alias can produce an incomplete subset of that artist's songs.
    # Repeat by its verified ID instead of dropping an article or fuzzy matching.
    alias = matches and any(_norm(c['artist']['name']) != _norm(metadata['artist']) for r in matches for c in r['artist-credit'])
    if not identifier and (alias or (not records)):
        if not artist_ids:
            artist_ids, names = _artist_lookup(client, metadata['artist'])
        title = _lookup_title(metadata, names)
        audio = _audio_context(metadata, names, _search_title(title))
        versions, details = _context_versions(title, audio, names)
        records = _search(client, title, metadata['artist'], versions, artist_ids)
        matches = [r for r in records if _matches(r, metadata, title, versions, details, artist_ids)]
    if not identifier and not matches and records:
        from .artist_credit_match import resolve_artist_credit
        credit_resolution = resolve_artist_credit(client, records, metadata, title, versions, details)
        if credit_resolution:
            artist_ids = credit_resolution['artistIds']
            names += credit_resolution['names']
            title = _lookup_title(metadata, names)
            audio = _audio_context(metadata, names, _search_title(title))
            versions, details = _context_versions(title, audio, names)
            # Resolve by complete credited identity, not a shortened name which
            # might return only part of the catalogue. Keep input metadata intact.
            records = _search(client, title, metadata['artist'], versions, artist_ids)
            matches = [r for r in records if _matches(r, metadata, title, versions, details, artist_ids)]
    return matches, title, versions, audio, credit_resolution


def _eligible(release, versions, artist_ids, supplied_album):
    group = release.get('release-group') or {}
    if not isinstance(group, dict) or not _UUID.fullmatch(str(group.get('id') or '')):
        return False
    if release.get('status', '').lower() != 'official' or group.get('primary-type', '').lower() not in ('album', 'ep'):
        return False
    secondary = {str(t).lower() for t in group.get('secondary-types', [])}
    permitted = versions & {'live', 'remix', 'demo'}
    if (secondary & _EXCLUDE) - permitted or ('live' in versions and 'live' not in secondary):
        return False
    if _ids(release) and _ids(release) != artist_ids:
        return False
    if supplied_album and _norm(supplied_album) not in {_norm(group.get('title')), _norm(release.get('title'))}:
        return False
    return True


def _group_nominations(client, records, metadata, versions):
    """Collect once, retaining EPs for a possible later verification phase."""
    groups = {}
    for record in records:
        # Search embeds linked releases but no completeness promise. They only
        # nominate albums; a selected original edition is independently checked.
        releases = record.get('releases')
        if releases is None:
            releases = _pages(client, 'release', {'recording': record['id'], 'inc': 'release-groups+artist-credits',
                                                'status': 'official', 'type': 'album|ep'}, 'releases', 'release-count', MAX_RELEASES)
        if not isinstance(releases, list):
            raise _LookupFailure('incomplete_catalogue', 'ambiguous')
        for release in releases:
            if not isinstance(release, dict) or not _eligible(release, versions, _ids(record), metadata.get('album')):
                continue
            group = release['release-group']
            release_type = 'Album' if group['primary-type'].lower() == 'album' else 'EP'
            entry = groups.setdefault(group['id'], {'id': group['id'], 'releases': {}, 'records': {},
                                                   'artistIds': _ids(record), 'releaseType': release_type})
            if entry['releaseType'] != release_type or entry['artistIds'] != _ids(record):
                raise _LookupFailure('album_identity_unconfirmed', 'ambiguous')
            entry['releases'][release['id']] = release
            entry['records'][record['id']] = record
    return list(groups.values())


def _verify_groups(client, groups, metadata, versions):
    # The cap applies to candidates needed for this phase. EP nominations do
    # not consume album slots, but share the same overall HTTP/time budget.
    if len(groups) > MAX_GROUPS:
        raise _LookupFailure('album_ambiguous', 'ambiguous')
    result = []
    for group in groups:
        full = client.json(f"https://musicbrainz.org/ws/2/release-group/{group['id']}?fmt=json&inc=artist-credits", 'mb')
        if not full or full.get('id') != group['id'] or _ids(full) != group['artistIds']:
            raise _LookupFailure('album_identity_unconfirmed', 'ambiguous')
        if str(full.get('primary-type', '')).lower() != group['releaseType'].lower():
            raise _LookupFailure('album_identity_unconfirmed', 'ambiguous')
        probe = {'status': 'Official', 'release-group': full, 'artist-credit': full.get('artist-credit')}
        if not _eligible(probe, versions, group['artistIds'], metadata.get('album')):
            continue
        group.update(album=full['title'], interval=_interval(full.get('first-release-date')),
                     dates={full['first-release-date']})
        result.append(group)
    return result


def _choose(groups, metadata, versions, audio):
    if not groups:
        raise _LookupFailure('album_not_found')
    # Prefer the song's album; an EP is a fallback for an EP-only release.
    if not metadata.get('album') and any(g['releaseType'] == 'Album' for g in groups):
        groups = [g for g in groups if g['releaseType'] == 'Album']
    if 'live' in versions and not metadata.get('album'):
        # Specific named sessions supplied by the source/audio narrow live
        # releases. Generic "live" alone cannot pick between different concerts.
        fragments = re.findall(r'[\[(]([^\])]+)[\])]', audio + ' ' + metadata['title'])
        fragments = [_norm(f) for f in fragments if len(_norm(f)) >= 8]
        contextual = [g for g in groups if any(f in _norm(g['album']) for f in fragments)]
        if contextual:
            groups = contextual
    if len(groups) == 1:
        return groups[0]
    if metadata.get('album') or 'live' in versions:
        raise _LookupFailure('album_ambiguous', 'ambiguous')
    ordered = sorted(groups, key=lambda g: g['interval'][0])
    if ordered[0]['interval'][1] >= ordered[1]['interval'][0]:
        raise _LookupFailure('album_ambiguous', 'ambiguous')
    return ordered[0]


def _complete_tracklist(release):
    """Require explicit complete track counts before using negative evidence."""
    media = release.get('media')
    if not isinstance(media, list) or not media:
        return False
    if 'media-count' in release and release['media-count'] != len(media):
        return False
    if any(not isinstance(m, dict) for m in media):
        return False
    if [m.get('position') for m in media] != list(range(1, len(media) + 1)):
        return False
    for medium in media:
        tracks = medium.get('tracks')
        count = medium.get('track-count')
        if type(count) is not int or count <= 0 or not isinstance(tracks, list) or count != len(tracks):
            return False
        if any(not isinstance(t, dict) for t in tracks):
            return False
        if [t.get('position') for t in tracks] != list(range(1, count + 1)):
            return False
        for track in tracks:
            record = track.get('recording')
            if (not isinstance(record, dict) or not _UUID.fullmatch(str(record.get('id', '')))
                    or not isinstance(record.get('title'), str) or not record['title'].strip() or not _ids(record)):
                return False
    return True


def _reconcile_official_editions(client, group, versions):
    # Browse the whole group, never just editions linked to this song: the
    # latter could all be bonus reissues. Unknown-status dates do not establish
    # the first Official release; undated Official editions prevent certainty.
    releases = _pages(client, 'release', {'release-group': group['id'],
                      'inc': 'artist-credits+release-groups'}, 'releases', 'release-count', MAX_RELEASES)
    official = []
    for release in releases:
        linked = release.get('release-group') or {}
        if linked.get('id') != group['id']:
            raise _LookupFailure('album_identity_unconfirmed', 'ambiguous')
        if release.get('status') != 'Official':
            continue
        if (_ids(release) != group['artistIds'] or
                str(linked.get('primary-type', '')).lower() != group['releaseType'].lower() or
                not _eligible(release, versions, group['artistIds'], None)):
            raise _LookupFailure('album_identity_unconfirmed', 'ambiguous')
        try:
            interval = _interval(release.get('date'))
        except _LookupFailure:
            raise _LookupFailure('album_track_unconfirmed', 'ambiguous') from None
        official.append((interval, release))
    if not official:
        raise _LookupFailure('album_track_unconfirmed', 'ambiguous')
    interval = (min(i[0] for i, r in official), min(i[1] for i, r in official))
    if interval[0].year > group['interval'][0].year:
        # A catalogue containing only a later reissue is not proof that its
        # bonus tracks were original. Explain the earlier summary date with a
        # listed non-Official edition before advancing to the Official year.
        explained = False
        for release in releases:
            if release.get('status') == 'Official':
                continue
            try:
                observed = _interval(release.get('date'))
            except _LookupFailure:
                continue
            if observed == group['interval']:
                explained = True
        if not explained:
            raise _LookupFailure('album_track_unconfirmed', 'ambiguous')
    editions = [r for i, r in official if i[0].year == interval[0].year]
    group['releaseDateResolution'] = {
        'basis': 'complete-group-earliest-official-year',
        'reportedFirstReleaseDate': sorted(group['dates'])[0],
        'officialDateInterval': [d.isoformat() for d in interval],
        'officialYear': interval[0].year, 'releaseCount': len(releases),
        'officialReleaseCount': len(official), 'earliestYearReleaseIds': [r['id'] for r in editions],
    }
    group['interval'] = interval
    group['officialEditions'] = editions
    # Reconciled editions can also supply artwork, without changing track proof.
    group['releases'].update({r['id']: r for r in editions})
    return editions


def _track_editions(client, group, metadata, versions):
    # An original-year edition avoids mistaking a later bonus track for a song
    # originally on this album. Supplied albums may intentionally be reissues.
    if 'officialEditions' in group:
        return group['officialEditions']
    year = group['interval'][0].year
    editions = list(group['releases'].values())
    if not metadata.get('album'):
        editions = [r for r in editions if str(r.get('date', '')).startswith(str(year))]
    if not editions:
        query = 'rgid:' + group['id'] + ' AND status:official'
        if not metadata.get('album'):
            query += f' AND date:[{year}-01-01 TO {year}-12-31]'
        editions = _pages(client, 'release', {'query': query}, 'releases', 'count', 100)
    if not editions and not metadata.get('album'):
        editions = _reconcile_official_editions(client, group, versions)
    return editions


def _choose_track_editions(client, groups, metadata, versions, audio):
    # A corrected date can change which group is earliest. Re-rank before
    # accepting its track or cover; each group can be reconciled only once.
    for _ in range(len(groups) + 1):
        group = _choose(groups, metadata, versions, audio)
        previous = group['interval']
        editions = _track_editions(client, group, metadata, versions)
        if group['interval'] == previous:
            return group, editions
    raise _LookupFailure('album_ambiguous', 'ambiguous')


def _confirm_track(client, group, metadata, title, versions, editions):
    year = group['interval'][0].year
    editions = sorted(editions, key=lambda r: (str(r.get('date') or '9999'), r['id']))
    checked = []
    inconclusive = False
    for edition in editions[:MAX_TRACK_EDITIONS]:
        url = f"https://musicbrainz.org/ws/2/release/{edition['id']}?fmt=json&inc=recordings+artist-credits+release-groups"
        full = client.json(url, 'mb')
        if client.last_json_cached and (not full or (full.get('release-group') or {}).get('id') != group['id'] or
                not any((t.get('recording') or {}).get('id') in group['records'] for m in full.get('media', []) for t in m.get('tracks', []))):
            full = client.json(url, 'mb', fresh=True)
        if not full or full.get('id') != edition['id'] or full.get('status') != 'Official':
            inconclusive = True
            continue
        if (full.get('release-group') or {}).get('id') != group['id'] or _ids(full) != group['artistIds']:
            inconclusive = True
            continue
        if not metadata.get('album') and not str(full.get('date', '')).startswith(str(year)):
            inconclusive = True
            continue
        for medium in full.get('media', []):
            for track in medium.get('tracks', []):
                record = track.get('recording') or {}
                # Original editions and remasters can have different recording
                # IDs. Album matching may use the same exact artist/song/version
                # on the independently verified original release, never a bonus
                # track, cover artist or different performance.
                compatible = _matches(record, metadata, title, versions, _title_identity(title)[2], group['artistIds'])
                if record.get('id') in group['records'] or (not metadata.get('musicbrainzRecordingId') and compatible):
                    group['release'] = full
                    group['trackEvidence'] = {'releaseId': full['id'], 'recordingId': record['id'],
                                              'medium': medium.get('position'), 'track': track.get('position'),
                                              'basis': 'supplied-album-track' if metadata.get('album') else 'original-year-release-track'}
                    group['trackEvidence']['recordingMatch'] = 'linked-id' if record['id'] in group['records'] else 'exact-artist-title-version'
                    return
        if _complete_tracklist(full):
            checked.append(full['id'])
        else:
            inconclusive = True
    if checked and not inconclusive:
        raise _CheckedTrackMismatch({
            'releaseGroupId': group['id'], 'album': group['album'],
            'reason': 'track-not-found-in-checked-original-editions',
            'checkedReleaseIds': checked, 'candidateEditionCount': len(editions),
            'editionLimitReached': len(editions) > MAX_TRACK_EDITIONS,
        })
    raise _LookupFailure('album_track_unconfirmed', 'ambiguous')


def album_label_candidate(metadata):
    """A trailing label is only a hypothesis until album membership is proven."""
    if metadata.get('musicbrainzRecordingId'):
        return None
    match = re.fullmatch(r'([^\[\]()]+?)\s*(?:\[([^\[\]()]+)\]|\(([^\[\]()]+)\))', metadata['title'])
    if not match:
        return None
    title, label = match[1].strip(), (match[2] or match[3]).strip()
    if not _norm(title) or not _norm(label):
        return None
    # Performance labels already have their own artwork-only fallback. Do not
    # reinterpret them as album metadata, even for an album called "Live".
    if any(re.search(pattern, label, re.I) for pattern in _VERSIONS.values()):
        return None
    if metadata.get('album') and _norm(metadata['album']) != _norm(label):
        return None
    return title, label


def select_album_with_label(client, metadata):
    """Exact lookup first; retry a semantic miss with a verified album label."""
    try:
        return select_album(client, metadata)
    except _LookupFailure as exc:
        candidate = album_label_candidate(metadata) if exc.reason == 'recording_not_found' else None
        if candidate is None:
            raise
    title, label = candidate
    # Retain audio/version context and any explicit album. Inferred labels do
    # not grant the reissue permission that an explicitly supplied album has.
    group = select_album(client, {**metadata, 'title': title}, album_hint=label)
    group['albumLabelResolution'] = {
        'sourceTitle': metadata['title'], 'lookupTitle': title, 'label': label,
        'basis': 'exact-album-label-and-verified-track',
        'releaseGroupId': group['id'], 'album': group['album'],
    }
    return group


def select_album(client, metadata, *, album_hint=None):
    records, title, versions, audio, credit_resolution = _recordings(client, metadata)
    if not records:
        raise _LookupFailure('recording_not_found')
    nominations = _group_nominations(client, records, metadata, versions)
    # Explicit names/labels must still compare all matching release types.
    # Otherwise apply the existing Album-before-EP ranking before HTTP work.
    staged = not metadata.get('album') and album_hint is None
    deferred_eps = [g for g in nominations if g['releaseType'] == 'EP'] if staged else []
    first = [g for g in nominations if g['releaseType'] == 'Album'] if staged else nominations
    groups = _verify_groups(client, first, metadata, versions)
    if staged and not groups:
        groups = _verify_groups(client, deferred_eps, metadata, versions)
        deferred_eps = []
    if album_hint is not None:
        groups = [g for g in groups if _norm(g['album']) == _norm(album_hint)]
        # A label cannot choose between multiple independently verified groups
        # sharing a name. Never use date ordering to resolve that uncertainty.
        if len(groups) > 1:
            raise _LookupFailure('album_ambiguous', 'ambiguous')
    group, editions = _choose_track_editions(client, groups, metadata, versions, audio)
    try:
        _confirm_track(client, group, metadata, title, versions, editions)
    except _CheckedTrackMismatch as exc:
        # A later deluxe edition may nominate an album for an EP song. Bounded
        # original-album checks do not prove absence from every edition. They
        # permit a positively verified EP fallback, never an unverified album
        # guess. Explicit album requests and live-session ambiguity stay strict.
        if metadata.get('album') or 'live' in versions or group['releaseType'] != 'Album':
            raise
        eps = (_verify_groups(client, deferred_eps, metadata, versions) if staged else
               [candidate for candidate in groups if candidate['releaseType'] == 'EP'])
        if not eps:
            raise
        group, editions = _choose_track_editions(client, eps, metadata, versions, audio)
        _confirm_track(client, group, metadata, title, versions, editions)
        group['albumCandidateChecks'] = [exc.evidence]
    group['lookupTitle'] = title
    group['versions'] = sorted(versions)
    if credit_resolution:
        group['artistCreditResolution'] = credit_resolution['proof']
    return group
