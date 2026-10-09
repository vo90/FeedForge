"""Authored pedal instructions, independent of attacks and scoring."""
from copy import deepcopy

MESSAGE = 'Wah open/closed instructions are preserved as optional timed guidance in the updated highway and tab view. Audio and scoring are unchanged.'


def perform(track, visits, at):
    result = []
    for occurrence, (index, origin) in enumerate(visits, 1):
        for voice in track.written_bars[index] if track.written_bars else []:
            for beat in voice.beats:
                if beat.wah is not None:
                    result.append(dict(t=round(at(origin + beat.position), 6), state=beat.wah,
                        source_id=beat.source_id, occurrence=occurrence,
                        source_ids=[n.source_id for n in beat.notes],
                        strings=sorted({n.string for n in beat.notes})))
    return sorted(result, key=lambda e: (e['t'], e['source_id']))


def export(events, alignment, end):
    from .alignment import map_time
    from .collapsed_opening import omitted
    result = []
    for e in events:
        if omitted(alignment, e['t']): continue
        time = map_time(alignment, e['t'], allow_negative=True)
        if 0 <= time < end: result.append({**deepcopy(e), 't': round(time, 6)})
    return result


def hybrid(plan, originals):
    """Use note lineage for played beats and passage ownership for rests."""
    def pairs(rows):
        return {(o, s) for r in rows for o in r.get('occurrences', []) for s in r.get('sourceIds', [])}
    def ids(e):
        return {(e['occurrence'], s) for s in [e['source_id'], *e['source_ids']]}
    kept, removed = pairs(plan.get('mainEvents', [])), pairs(plan.get('removedMain', []))
    passages = plan['passages']
    def within(e, p):
        return p.get('recordingOwnedStart', p['recordingStart']) - 1e-7 <= e['t'] < p.get('recordingOwnedEnd', p['recordingEnd']) - 1e-7
    result = []
    for e in originals[plan['mainTrackId']]['chart'].get('wah', []):
        if e['source_ids']:
            keep = not (ids(e) & removed) or bool(ids(e) & kept)
        else:
            keep = not any(within(e, p) for p in passages)
        if keep: result.append(deepcopy(e))
    for p in passages:
        selected = pairs(p['events'])
        for e in originals[p['trackId']]['chart'].get('wah', []):
            if within(e, p) and (not e['source_ids'] or ids(e) & selected):
                result.append(deepcopy(e))
    # Repeated passage receipts can refer to the same authored instruction.
    unique = {(e['t'], e['source_id'], e['occurrence']):e for e in result}
    return sorted(unique.values(), key=lambda e: (e['t'], e['source_id']))
