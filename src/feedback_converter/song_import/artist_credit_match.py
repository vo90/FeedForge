"""Conservative artwork-only resolution of shortened featured artist credits."""
import re

from .artwork import _LookupFailure, _UUID, _artist_names, _norm

MAX_CREDIT_CANDIDATES = 3
_FEATURED = re.compile(r"\s+(?:featuring|feat\.?|ft\.?)\s+", re.I)


def _primary_name(name):
    parts = _FEATURED.split(str(name), maxsplit=1)
    return parts[0].strip() if len(parts) == 2 and parts[1].strip() else ''


def _names(artist):
    return _artist_names({'artist-credit': [{'artist': artist}]})


def resolve_artist_credit(client, records, metadata, title, versions, details):
    # Import locally: the album matcher owns the existing song/version rules.
    from .album_match import _ids, _matches

    source = _norm(metadata['artist'])
    candidates = {}
    for record in records:
        ids = _ids(record)
        if not ids or not _matches(record, metadata, title, versions, details, ids):
            continue
        credits = record['artist-credit']
        first = credits[0]
        if len(credits) > 1:
            # "Artist feat. Guest", not any member of an unordered collaboration.
            if not _FEATURED.fullmatch(' ' + str(first.get('joinphrase', '')).strip() + ' '):
                continue
            if source not in _artist_names({'artist-credit': [{**first, 'joinphrase': ''}]}):
                continue
            kind = 'primary-featured-credit'
        elif source == _norm(_primary_name(first['artist'].get('name', ''))):
            kind = 'named-group-member'
        else:
            continue
        candidates.setdefault(ids, (kind, credits))
    if len(candidates) > MAX_CREDIT_CANDIDATES:
        raise _LookupFailure('artist_ambiguous', 'ambiguous')

    verified = []
    for ids, (kind, credits) in candidates.items():
        inc = 'aliases+artist-rels' if kind == 'named-group-member' else 'aliases'
        entity = client.json(f'https://musicbrainz.org/ws/2/artist/{ids[0]}?fmt=json&inc={inc}', 'mb')
        if not entity or entity.get('id') != ids[0]:
            continue
        proof = {'policy': 'verified-featured-artist-v1', 'basis': kind,
                 'sourceArtist': metadata['artist'], 'creditedArtistIds': list(ids)}
        if kind == 'primary-featured-credit':
            if source not in _names(entity):
                continue
            proof['primaryArtistId'] = entity['id']
        else:
            # The canonical group name AND its membership must corroborate the
            # short credit. Do not treat every band of a musician as equivalent.
            if entity.get('type') != 'Group' or source != _norm(_primary_name(entity.get('name', ''))):
                continue
            members = {}
            for relation in entity.get('relations', []):
                member = relation.get('artist') or {}
                if (relation.get('type') == 'member of band' and relation.get('direction') == 'backward'
                        and _UUID.fullmatch(str(member.get('id') or '')) and source in _names(member)):
                    members[member['id']] = relation
            if len(members) > 1:
                raise _LookupFailure('artist_ambiguous', 'ambiguous')
            if not members:
                continue
            identifier, relation = next(iter(members.items()))
            proof.update(primaryArtistId=identifier, creditedGroupName=entity['name'],
                         relationship={'type': 'member of band', 'direction': 'backward',
                                       'begin': relation.get('begin'), 'end': relation.get('end')})
        verified.append({'artistIds': ids, 'names': [c['artist']['name'] for c in credits], 'proof': proof})
    if len(verified) > 1:
        raise _LookupFailure('artist_ambiguous', 'ambiguous')
    return verified[0] if verified else None
