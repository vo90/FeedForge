"""Bounded recovery when recording-search offset pages overlap.

MusicBrainz recording search supports Lucene queries on `rid`. Disjoint UUID
prefixes retrieve the same query without relying on a stable relevance order.
This applies only to recording search, never release browse pagination.
"""
from .artwork import _LookupFailure, _UUID, _query_url

MAX_PARTITION_REQUESTS = 14


def recover_recording_pages(client, values, expected, observed_ids, cap):
    requests = 0

    def fail():
        raise _LookupFailure('incomplete_catalogue', 'ambiguous')

    def partition(prefixes):
        nonlocal requests
        if requests >= MAX_PARTITION_REQUESTS:
            fail()
        requests += 1
        # Parenthesize the complete original query, preserving every artist,
        # title and version restriction. Prefixes are internal hex constants.
        query = '(' + values['query'] + ') AND rid:(' + ' OR '.join(p + '*' for p in prefixes) + ')'
        page = client.json(_query_url('recording', {**values, 'query': query, 'limit': 100, 'offset': 0}), 'mb')
        if not isinstance(page, dict):
            fail()
        count, items = page.get('count'), page.get('recordings')
        if (type(count) is not int or not 0 <= count <= cap or page.get('offset') != 0
                or not isinstance(items, list) or len(items) != min(count, 100)):
            fail()
        ids = [str(item.get('id') or '').lower() if isinstance(item, dict) else '' for item in items]
        if (any(not _UUID.fullmatch(i) or i[0] not in prefixes for i in ids)
                or len(set(ids)) != len(ids)):
            fail()
        if count <= 100:
            return items
        # No unbounded recursion for pathological ID distributions. The shared
        # client also enforces the original overall request/time/rate limits.
        if len(prefixes) == 1:
            fail()
        midpoint = len(prefixes) // 2
        result = partition(prefixes[:midpoint]) + partition(prefixes[midpoint:])
        result_ids = {r['id'].lower() for r in result}
        if len(result) != count or len(result_ids) != count or not set(ids).issubset(result_ids):
            fail()
        return result

    if type(expected) is not int or not 100 < expected <= cap or len(observed_ids) > expected:
        fail()
    result = partition('01234567') + partition('89abcdef')
    result_ids = {r['id'].lower() for r in result}
    if len(result) != expected or len(result_ids) != expected or not observed_ids.issubset(result_ids):
        fail()
    return result
