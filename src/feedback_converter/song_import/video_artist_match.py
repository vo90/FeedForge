"""Bounded artwork artist corroboration from an exact selected video link."""
import re
from urllib.parse import urlencode

from . import album_match as m
from .artwork import _LookupFailure, _UUID

STREAM = '7e41ef12-a124-4324-afdb-fdbae687a89c'
MAX_TARGETS = 4


def fail():
    raise _LookupFailure('artist_ambiguous', 'ambiguous')


def streaming(relation, direction, target):
    return (isinstance(relation, dict)
            and relation.get('type-id') == STREAM
            and relation.get('type') == 'free streaming'
            and relation.get('direction') == direction
            and relation.get('target-type') == target
            and relation.get('ended') is False
            and relation.get('end') is None)


def resolve(client, metadata, matches, title, versions, details):
    """Corroborate an ambiguous artist; never claim to identify an audio master."""
    video = metadata.get('audioVideoId')
    if not isinstance(video, str) or not re.fullmatch(r'[A-Za-z0-9_-]{11}', video):
        return None
    url = 'https://www.youtube.com/watch?v=' + video
    query = urlencode({'resource': url, 'inc': 'recording-rels', 'fmt': 'json'})
    index = client.json('https://musicbrainz.org/ws/2/url/?' + query, 'mb')
    if index is None:
        return None
    if (not isinstance(index, dict) or not _UUID.fullmatch(str(index.get('id', '')))
            or index.get('resource') != url or not isinstance(index.get('relations'), list)):
        fail()
    # Avoid discarding a possible conflicting recording due to malformed flags.
    targets = {}
    for rel in index['relations']:
        if not isinstance(rel, dict):
            fail()
        if rel.get('target-type') != 'recording' or rel.get('type-id') != STREAM:
            continue
        if rel.get('ended') is True:
            continue
        if not streaming(rel, 'backward', 'recording'):
            fail()
        item = rel.get('recording')
        if not isinstance(item, dict) or not _UUID.fullmatch(str(item.get('id', ''))):
            fail()
        identifier = item['id']
        if identifier in targets and targets[identifier] != item:
            fail()
        targets[identifier] = item
    if not targets:
        return None
    if len(targets) > MAX_TARGETS:
        fail()
    candidate_ids = {m._ids(r) for r in matches}
    observed = set()
    audio = []
    for identifier in sorted(targets):
        record = client.json(
            f'https://musicbrainz.org/ws/2/recording/{identifier}?fmt=json&inc=artist-credits+url-rels', 'mb')
        if not isinstance(record, dict) or record.get('id') != identifier:
            fail()
        rels = record.get('relations')
        if not isinstance(rels, list) or any(not isinstance(r, dict) for r in rels):
            fail()
        if not any(streaming(r, 'forward', 'url') and isinstance(r.get('url'), dict)
                   and r['url'].get('resource') == url for r in rels):
            fail()
        if not isinstance(record.get('video'), bool):
            fail()
        credits = record.get('artist-credit')
        if (not isinstance(credits, list) or not credits or any(
                not isinstance(c, dict) or not isinstance(c.get('artist'), dict)
                or not isinstance(c['artist'].get('name'), str) for c in credits)
                or not m._ids(record) or not isinstance(record.get('title'), str)):
            fail()
        # Video entities can reveal conflicting artist credits, but only audio
        # entities can provide positive evidence for this conservative first pass.
        check = {**record, 'video': False}
        if not m._matches(check, metadata, title, versions, details):
            continue
        identity = m._ids(record)
        observed.add(identity)
        if not record['video']:
            audio.append(record)
    if len(observed) != 1:
        fail()
    identity = next(iter(observed))
    if identity not in candidate_ids or not audio:
        return None
    return {'artistIds': identity, 'proof': {
        'basis': 'exact-selected-video-artist-corroboration', 'videoId': video,
        'urlId': index['id'], 'recordingIds': sorted(r['id'] for r in audio),
        'checkedRecordingIds': sorted(targets), 'artistIds': list(identity),
        'audioRecordingVerified': False,
    }}
