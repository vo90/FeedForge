"""Bounded album-level matching; never claims to identify an audio master."""
from calendar import monthrange
from datetime import date
import re

from .artwork import (_LookupFailure, _norm, _artist_names, _title_identity,
                      _search_title, _query_url, _pages, _UUID, _EXCLUDE)

MAX_RECORDINGS = 300
MAX_GROUPS = 8
MAX_RELEASES = 300


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
    return versions, details


def _search(client, title, artist, versions, artist_ids=()):
    who = ' AND '.join('arid:' + i for i in artist_ids) if artist_ids else 'artist:' + _quote(artist)
    query = 'recording:' + _quote(_search_title(title)) + ' AND ' + who
    query += ' AND status:official AND primarytype:(album OR ep) AND -video:true'
    for tag in ('live', 'demo', 'remix'):
        if tag not in versions:
            query += ' AND -comment:' + tag
    return _pages(client, 'recording', {'query': query}, 'recordings', 'count', MAX_RECORDINGS)


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
    return matches, title, versions, audio


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


def _group_candidates(client, records, metadata, versions):
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
            entry = groups.setdefault(group['id'], {'id': group['id'], 'releases': {}, 'records': {}, 'artistIds': _ids(record)})
            entry['releases'][release['id']] = release
            entry['records'][record['id']] = record
    if len(groups) > MAX_GROUPS:
        raise _LookupFailure('album_ambiguous', 'ambiguous')
    result = []
    for group in groups.values():
        full = client.json(f"https://musicbrainz.org/ws/2/release-group/{group['id']}?fmt=json&inc=artist-credits", 'mb')
        if not full or full.get('id') != group['id'] or _ids(full) != group['artistIds']:
            raise _LookupFailure('album_identity_unconfirmed', 'ambiguous')
        probe = {'status': 'Official', 'release-group': full, 'artist-credit': full.get('artist-credit')}
        if not _eligible(probe, versions, group['artistIds'], metadata.get('album')):
            continue
        group.update(album=full['title'], interval=_interval(full.get('first-release-date')),
                     dates={full['first-release-date']}, releaseType=full['primary-type'])
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


def _confirm_track(client, group, metadata, title, versions):
    # An original-year edition avoids mistaking a later bonus track for a song
    # originally on this album. Supplied albums may intentionally be reissues.
    year = group['interval'][0].year
    editions = list(group['releases'].values())
    if not metadata.get('album'):
        editions = [r for r in editions if str(r.get('date', '')).startswith(str(year))]
    if not editions:
        query = 'rgid:' + group['id'] + ' AND status:official'
        if not metadata.get('album'):
            query += f' AND date:[{year}-01-01 TO {year}-12-31]'
        editions = _pages(client, 'release', {'query': query}, 'releases', 'count', 100)
    editions = sorted(editions, key=lambda r: (str(r.get('date') or '9999'), r['id']))
    for edition in editions[:3]:
        url = f"https://musicbrainz.org/ws/2/release/{edition['id']}?fmt=json&inc=recordings+artist-credits+release-groups"
        full = client.json(url, 'mb')
        if client.last_json_cached and (not full or (full.get('release-group') or {}).get('id') != group['id'] or
                not any((t.get('recording') or {}).get('id') in group['records'] for m in full.get('media', []) for t in m.get('tracks', []))):
            full = client.json(url, 'mb', fresh=True)
        if not full or full.get('id') != edition['id'] or full.get('status') != 'Official':
            continue
        if (full.get('release-group') or {}).get('id') != group['id'] or _ids(full) != group['artistIds']:
            continue
        if not metadata.get('album') and not str(full.get('date', '')).startswith(str(year)):
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
    raise _LookupFailure('album_track_unconfirmed', 'ambiguous')


def select_album(client, metadata):
    records, title, versions, audio = _recordings(client, metadata)
    if not records:
        raise _LookupFailure('recording_not_found')
    groups = _group_candidates(client, records, metadata, versions)
    group = _choose(groups, metadata, versions, audio)
    _confirm_track(client, group, metadata, title, versions)
    group['lookupTitle'] = title
    group['versions'] = sorted(versions)
    return group
