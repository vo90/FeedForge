"""Validation for optional, non-scored pedal instructions."""
import math
from copy import deepcopy


def validate(events, strings=8):
    if not isinstance(events, list) or len(events) > 100000:
        raise ValueError('wah must be a bounded list')
    previous = -1
    for e in events:
        if not isinstance(e, dict) or type(e.get('t')) not in (float, int) or not math.isfinite(e['t']) or e['t'] < previous:
            raise ValueError('wah events must have sorted nonnegative finite times')
        if e['t'] < 0 or e.get('state') not in ('open', 'closed'):
            raise ValueError('invalid wah instruction')
        if (not isinstance(e.get('strings'), list) or len(e['strings']) > strings or
                any(type(s) is not int or not 0 <= s < strings for s in e['strings']) or
                len(set(e['strings'])) != len(e['strings'])):
            raise ValueError('invalid wah strings')
        if (type(e.get('occurrence')) is not int or e['occurrence'] < 1 or
                not isinstance(e.get('source_id'), str) or not 0 < len(e['source_id']) <= 200 or
                not isinstance(e.get('source_ids'), list) or len(e['source_ids']) > 64 or
                any(not isinstance(s, str) or not 0 < len(s) <= 200 for s in e['source_ids'])):
            raise ValueError('invalid wah lineage')
        previous = e['t']
    return deepcopy(events)
