"""Independent checks of pedal-event preservation and Hybrid selection."""


def compare(expected, actual, check, where):
    if not isinstance(actual, list):
        check.fail('wah', where, 'Wah guidance must be a list.'); return
    check.equal('wah_count', where, len(expected), len(actual))
    for i, (a, b) in enumerate(zip(expected, actual)):
        if not isinstance(b, dict):
            check.fail('wah', where, 'Invalid wah event.'); continue
        check.near('wah_time', f'{where}/wah/{i}/t', a['t'], b.get('t'))
        check.equal('wah_instruction', f'{where}/wah/{i}',
                    {k:v for k,v in a.items() if k != 't'}, {k:v for k,v in b.items() if k != 't'})


def hybrid(receipt, charts):
    def owns(event, rows):
        return any(event['occurrence'] in row.get('occurrences', []) and
                   any(s in row.get('sourceIds', []) for s in [event['source_id'], *event['source_ids']])
                   for row in rows)
    def in_passage(event, passage):
        a = passage.get('recordingOwnedStart', passage['recordingStart'])
        b = passage.get('recordingOwnedEnd', passage['recordingEnd'])
        return a - 1e-7 <= event['t'] < b - 1e-7
    selected = {}
    for track, chart in charts.items():
        for event in chart.get('wah', []):
            include = False
            if track == receipt['mainTrackId']:
                include = (owns(event, receipt.get('mainEvents', [])) or
                           not owns(event, receipt.get('removedMain', []))) if event['source_ids'] else (
                    not any(in_passage(event, p) for p in receipt['passages']))
            for passage in receipt['passages']:
                if track == passage['trackId'] and in_passage(event, passage):
                    include |= not event['source_ids'] or owns(event, passage['events'])
            if include:
                selected[event['t'], event['source_id'], event['occurrence']] = event
    return sorted(selected.values(), key=lambda e:(e['t'],e['source_id']))
