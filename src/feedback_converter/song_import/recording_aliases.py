"""Exact catalogue-authored recording aliases for optional album artwork."""
from .artwork import _LookupFailure, _pages, _search_title, _title_identity

MAX_ALIAS_CANDIDATES = 8


def resolve(client, metadata, title, versions, details, artist_ids=()):
    from .album_match import _quote, _ids, _matches

    who = ' AND '.join('arid:' + i for i in artist_ids) if artist_ids else 'artist:' + _quote(metadata['artist'])
    query = 'alias:' + _quote(_search_title(title)) + ' AND ' + who
    query += ' AND status:official AND primarytype:(album OR ep) AND -video:true'
    for tag in ('live', 'demo', 'remix'):
        if tag not in versions:
            query += ' AND -comment:' + tag
    # Never select the first few rows of an incomplete candidate set.
    candidates = _pages(client, 'recording', {'query': query}, 'recordings', 'count', MAX_ALIAS_CANDIDATES)
    matches, proofs = [], {}
    for candidate in candidates:
        full = client.json(f"https://musicbrainz.org/ws/2/recording/{candidate['id']}?fmt=json&inc=artist-credits+aliases", 'mb')
        if (not full or full.get('id') != candidate['id'] or not _ids(full)
                or _ids(full) != _ids(candidate) or not isinstance(full.get('title'), str)
                or not full['title'].strip()
                or _title_identity(full['title'], full.get('disambiguation', '')) !=
                   _title_identity(candidate.get('title', ''), candidate.get('disambiguation', ''))):
            raise _LookupFailure('invalid_metadata')
        aliases = full.get('aliases', [])
        if (not isinstance(aliases, list) or any(not isinstance(a, dict)
                or not isinstance(a.get('name'), str) or not a['name'].strip()
                or len(a['name']) > 1000 for a in aliases)):
            raise _LookupFailure('invalid_metadata')
        # Canonical qualifiers remain binding even when the alias omits them.
        if not _matches(full, metadata, full['title'], versions, details, artist_ids):
            continue
        exact = sorted({a['name'] for a in aliases if
            _matches({**full, 'title': a['name']}, metadata, title, versions, details, artist_ids)})
        if not exact:
            continue
        record = dict(full)
        if 'releases' in candidate:
            record['releases'] = candidate['releases']
        matches.append(record)
        proofs[record['id']] = {'recordingId': record['id'], 'sourceTitle': title,
            'canonicalTitle': full['title'], 'aliases': exact, 'artistIds': list(_ids(full)),
            'basis': 'exact-catalogue-recording-alias'}
    if len({_ids(r) for r in matches}) > 1:
        raise _LookupFailure('artist_ambiguous', 'ambiguous')
    if not hasattr(client, 'recording_aliases'):
        client.recording_aliases = {}
    client.recording_aliases.update(proofs)
    return matches
