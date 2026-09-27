"""Independent primary-role and event-coverage audit for Hybrid Lead v2.

This module never imports the producer's planner, context or materializer.
Written facts and directional technique edges come from the independent source
reader. Coverage is checked even when a forged output has no silence at all.
"""
from collections import defaultdict
import math
import re

TOL = 0.0000011
# Source chart timestamps are rounded to microseconds. Inverting a fast tempo
# can move an exact handover by several millionths of a quarter-note beat.
EPS = 1e-5


def _touch(a, b):
    return a[0] < b[1] - EPS and a[1] > b[0] + EPS or a[0] == a[1] and b[0] - EPS <= a[0] < b[1] - EPS


def _joined(spans, gap=0):
    out = []
    for a, b in sorted(spans, key=lambda span: (span[0],span[1])):
        if out and (a <= out[-1][1] + EPS if gap == 0 else a - out[-1][1] < gap - EPS):
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def _events(chart, part, quarter_at, duration, source_format):
    facts = defaultdict(list)
    beats = defaultdict(list)
    for row in part['notes']:
        n = row['note']
        facts[(n['s'], n['f'], math.floor(n['t'] * 100000))].append(row)
    for b in part['notation_beats']:
        beats[b['location']].append(b)
    beat_ids = {b['location']: b.get('source_id') for bar in part['source'].beats for b in bar}
    def source_id(location):
        if source_format == 'songsterr':
            return 'songsterr:' + ':'.join(re.findall(r'\d+', location))
        if location in beat_ids:
            return beat_ids[location]
        parent, _, index = location.rsplit('/', 2)
        return beat_ids[parent] + ':' + index
    events, atoms = {}, []
    for kind in ('notes', 'chords'):
        for index, event in enumerate(chart[kind]):
            key = kind, index
            members = [event] if kind == 'notes' else [{'t': event['t'], **n} for n in event['notes']]
            starts, ends, matched, source_beats = [], [], [], []
            for n in members:
                starts.append(n['t']); ends.append(n['t'] + n.get('sus', 0))
                bucket = math.floor(n['t'] * 100000)
                matches = [row for b in (bucket-1, bucket, bucket+1) for row in facts[(n['s'], n['f'], b)] if abs(row['note']['t'] - n['t']) <= TOL]
                matched.extend(matches)
                voice = set()
                for match in matches:
                    locations = {loc.rsplit('/', 2)[0] for loc in match['locations']}
                    for location in locations:
                        candidates = beats[location]
                        selected = [b for b in candidates if b['time'] <= n['t'] + n.get('sus',0) + TOL and b['end'] >= n['t'] - TOL]
                        if not selected and candidates:
                            # Staccato can end the sound before a tied written
                            # continuation. Its source relationship still reserves
                            # that slot in this performed occurrence.
                            selected = [min(candidates, key=lambda b: (b['measure'] < match['occurrence'], abs(b['time']-n['t'])))]
                        for b in selected:
                            starts.append(float(b['time'])); ends.append(min(duration, float(b['end'])))
                            voice.add(b['voice']); source_beats.append(b)
                    # Read the directional effect from independently rendered
                    # source facts, not the producer's declared gesture range.
                    atoms.append((match['note']['t'], key, tuple(sorted(voice)) or (0,), match['note']))
            ids = {source_id(loc) for m in matched for loc in m['locations']}
            if kind == 'chords':
                ids.update(source_id(m['beat']) for m in matched)
            occurrences = {b['measure'] for b in source_beats}
            events[key] = {'start': quarter_at(min(starts)), 'end': quarter_at(max(ends)), 'notes': members,
                           'sourceIds': sorted(ids), 'occurrences': sorted(occurrences)}
    parent = {key: key for key in events}
    def root(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k
    written = sorted(part['notation_beats'], key=lambda b: (b['time'],b['voice']))
    for grace in (b for b in written if b['notation'].get('grace')):
        principal = next((b for b in written if b['voice'] == grace['voice'] and not b['rest']
                          and not b['notation'].get('grace') and b['time'] >= grace['end']-TOL),None)
        if principal:
            prefixes = (source_id(grace['location'])+':',source_id(principal['location'])+':')
            linked = [k for k,r in events.items() if any(s.startswith(prefixes) for s in r['sourceIds'])
                      and r['start'] < quarter_at(principal['end'])+EPS and r['end'] > quarter_at(grace['time'])-EPS]
            for key in linked:
                events[key]['start'] = min(events[key]['start'],quarter_at(grace['time']))
                events[key]['end'] = max(events[key]['end'],quarter_at(min(duration,principal['end'])))
            for key in linked[1:]:
                parent[root(key)] = root(linked[0])
    previous = {}
    for _, key, voice, note in sorted(atoms, key=lambda a: (a[0], a[1], a[3]['s'])):
        string = voice, note['s']
        prior = previous.get(string)
        if prior and prior[0] != key:
            old_key, origin = prior
            if origin.get('ln') or origin.get('sl') is not None or origin.get('slu') or note.get('ho') or note.get('po'):
                parent[root(key)] = root(old_key)
        previous[string] = key, note
    spans = {}
    for key, row in events.items():
        item = spans.setdefault(root(key), [row['start'], row['end']])
        item[:] = min(item[0], row['start']), max(item[1], row['end'])
    for key, row in events.items():
        row['start'], row['end'] = spans[root(key)]
    return events


def audit(charts, receipt, facts, options, quarter_at, recording_at, duration, source_format, check):
    main_id = options['mainTrackId']
    parts = {p['source'].id: p for p in facts['parts']}
    rows = {tid: _events(chart, parts[tid], quarter_at, duration, source_format) for tid, chart in charts.items() if tid in parts and parts[tid]['source'].instrument == 'guitar'}
    for tid,part in parts.items():
        if part['source'].instrument == 'guitar':
            rows.setdefault(tid,{})
    roles = {}
    for tid in rows:
        name = parts[tid]['source'].name
        if tid == main_id:
            role = 'main'
        elif tid in options.get('excludedTrackIds', []):
            role = 'excluded'
        elif tid in options.get('roles', {}):
            role = options['roles'][tid]
        elif re.search(r'\b(delay|echo|effect|fx)\b', name, re.I):
            role = 'accompaniment'
        elif re.search(r'\bsolo\b', name, re.I) and not re.search(r'\b(chords?|rhythm|harmon(?:y|ies))\b', name, re.I):
            role = 'solo'
        elif re.search(r'\blead\b', name, re.I) and not re.search(r'\bsolo\b', name, re.I):
            role = 'lead'
        elif not re.search(r'\bsolo\b', name, re.I) and re.search(r'\b(clean|rhythm|acoustic|classical|background|extra|overdubs?)\b', name, re.I):
            role = 'accompaniment'
        else:
            role = 'unresolved'
            check.fail('hybrid_primary_role', 'hybrid/roles', 'An ambiguous guitar role was not explicitly resolved.')
        roles[tid] = role
    check.equal('hybrid_primary_role', 'hybrid/roles', roles, receipt.get('roles'))
    preferred = options.get('preferredTrackIds', [])
    order = sorted(roles, key=lambda tid: (0 if roles[tid] == 'solo' else 1, preferred.index(tid) if tid in preferred else len(preferred), tid))
    expected_primary, regions, displaced_solos = set(), [], {}
    for tid in order:
        if roles[tid] != 'solo':
            continue
        if tid not in charts:
            if parts[tid]['notes']:
                check.fail('hybrid_primary_omission','hybrid/roles','A required solo has no playable original chart.')
            continue
        if charts[tid]['tuning'] != charts[main_id]['tuning'] or charts[tid].get('capo', 0) != charts[main_id].get('capo', 0):
            check.fail('hybrid_primary_setup', 'hybrid/roles', 'Required primary source has incompatible setup.')
        if any(n['note']['f'] > 24 and n['note']['f'] != 127 for n in parts[tid]['notes']):
            check.fail('hybrid_primary_omission', 'hybrid/roles', 'A required primary source contains unsupported frets.')
        for lo, hi in _joined([(r['start'], r['end']) for r in rows[tid].values()], gap=1):
            local = {k:r for k,r in rows[tid].items() if lo-EPS <= r['start'] and r['end'] <= hi+EPS}
            core = [r for r in local.values() if any(not n.get('ghost') and not n.get('mt') for n in r['notes'])]
            if not core:
                continue
            a, b = min(r['start'] for r in core), max(r['end'] for r in core)
            while True:
                touched = [r for r in local.values() if _touch((r['start'], r['end']), (a,b))]
                bounds = min([a]+[r['start'] for r in touched]), max([b]+[r['end'] for r in touched])
                if abs(bounds[0]-a) <= EPS and abs(bounds[1]-b) <= EPS:
                    break
                a,b = bounds
            blockers = [p for p in regions if _touch((a,b), (p[1],p[2]))]
            if blockers:
                if tid not in preferred and any(p[0] not in preferred for p in blockers):
                    check.fail('hybrid_primary_conflict', 'hybrid/roles', 'Overlapping solos lack an explicit priority.')
                for k in local:
                    displaced_solos[tid,*k] = sorted({p[0] for p in blockers})
                continue
            regions.append((tid,a,b))
            expected_primary.update((tid,*k) for k,r in local.items() if a-EPS <= r['start'] and r['end'] <= b+EPS and r['start'] < b-EPS)
    kept, removed = set(), set()
    for key, row in rows[main_id].items():
        (removed if any(_touch((row['start'],row['end']), (a,b)) for _,a,b in regions) else kept).add(key)
    slots = [(quarter_at(float(b['time'])),quarter_at(min(duration,float(b['end'])))) for b in parts[main_id]['notation_beats'] if not b['rest'] and float(b['time']) < duration]
    slots = [s for s in slots if not any(_touch(s,(a,b)) for _,a,b in regions) and not any(_touch(s,(rows[main_id][k]['start'],rows[main_id][k]['end'])) for k in removed)]
    main_spans = slots + [(rows[main_id][k]['start'],rows[main_id][k]['end']) for k in kept]
    protected = _joined(main_spans + [(a,b) for _,a,b in regions])
    lead_regions = []
    for tid in order:
        if roles[tid] != 'lead':
            continue
        if tid not in charts:
            if parts[tid]['notes']:
                check.fail('hybrid_primary_omission','hybrid/roles','A required lead has no playable original chart.')
            continue
        if charts[tid]['tuning'] != charts[main_id]['tuning'] or charts[tid].get('capo',0) != charts[main_id].get('capo',0):
            check.fail('hybrid_primary_setup','hybrid/roles','Required lead source has incompatible setup.')
        if any(n['note']['f'] > 24 and n['note']['f'] != 127 for n in parts[tid]['notes']):
            check.fail('hybrid_primary_omission', 'hybrid/roles', 'A required lead contains unsupported frets.')
        groups = defaultdict(list)
        for key,r in rows[tid].items():
            groups[(r['start'],r['end'])].append(key)
        for span,keys in sorted(groups.items()):
            peers = [p for p in lead_regions if p[0] != tid and _touch(span,(p[1],p[2]))]
            if peers and tid not in preferred and any(p[0] not in preferred for p in peers):
                check.fail('hybrid_primary_conflict','hybrid/roles','Overlapping additional leads lack an explicit priority.')
            if any(_touch(span,p) for p in protected):
                continue
            expected_primary.update((tid,*k) for k in keys)
            protected = _joined(protected+[span])
            lead_regions.append((tid,*span))
    actual_primary = {(p['trackId'],e['kind'],e['index']) for p in receipt['passages'] if p.get('priority') in {'solo','lead'} for e in p['events']}
    check.equal('hybrid_primary_coverage','hybrid/passages',sorted(expected_primary),sorted(actual_primary))
    for p in receipt['passages']:
        check.equal('hybrid_primary_priority','hybrid/passages/priority',roles.get(p['trackId']),p.get('priority'))
        if p.get('priority') in {'solo','lead'}:
            members = [rows[p['trackId']][(e['kind'],e['index'])] for e in p['events']]
            if members:
                check.near('hybrid_primary_boundary','hybrid/passages/start',min(r['start'] for r in members),p['start'],1e-5)
                check.near('hybrid_primary_boundary','hybrid/passages/end',max(r['end'] for r in members),p['end'],1e-5)
            check.equal('hybrid_primary_boundary','hybrid/passages/boundaries',['primary','primary'],p.get('boundaries'))
            check.equal('hybrid_primary_boundary','hybrid/passages/boundaryQuarters',[p['start'],p['end']],p.get('boundaryQuarters'))
    declared_kept = {(r['kind'],r['index']) for r in receipt.get('mainEvents',[])}
    declared_removed = {(r['kind'],r['index']) for r in receipt.get('removedMain',[])}
    check.equal('hybrid_main_coverage','hybrid/mainEvents',sorted(kept),sorted(declared_kept))
    check.equal('hybrid_main_coverage','hybrid/removedMain',sorted(removed),sorted(declared_removed))
    if len(declared_kept) != len(receipt.get('mainEvents',[])) or len(declared_removed) != len(receipt.get('removedMain',[])):
        check.fail('hybrid_main_coverage','hybrid/mainEvents','Duplicate main event reference.')
    for ref in receipt.get('mainEvents',[]) + receipt.get('removedMain',[]):
        original = rows[main_id].get((ref['kind'],ref['index']))
        if original:
            for field in ('sourceIds','occurrences'):
                check.equal('hybrid_event_lineage','hybrid/mainEvents/'+field,original[field],ref.get(field))
    for r in receipt.get('removedMain',[]):
        key=r['kind'],r['index']
        if key in rows[main_id]:
            check.near('hybrid_main_coverage','hybrid/removedMain/start',rows[main_id][key]['start'],r['start'],1e-5)
            check.near('hybrid_main_coverage','hybrid/removedMain/end',rows[main_id][key]['end'],r['end'],1e-5)
            check.near('hybrid_main_coverage','hybrid/removedMain/recordingStart',quarter_at(r['recordingStart']),r['start'],1e-5)
            check.near('hybrid_main_coverage','hybrid/removedMain/recordingEnd',quarter_at(r['recordingEnd']),r['end'],1e-5)
            targets = sorted({tid for tid,a,b in regions if _touch((r['start'],r['end']),(a,b))})
            check.equal('hybrid_main_coverage','hybrid/removedMain/supersededBy',targets,r.get('supersededBy'))
    check.equal('hybrid_coverage','hybrid/coverage/status','complete',receipt.get('coverage',{}).get('status'))
    selected = {(main_id,*k) for k in kept} | {(p['trackId'],r['kind'],r['index']) for p in receipt['passages'] for r in p['events']}
    ledger = {}
    for row in receipt.get('coverage',{}).get('events',[]):
        if row.get('index') is None and row.get('reason') == 'recording_end':
            continue
        key = row['trackId'],row['kind'],row['index']
        if key in ledger:
            check.fail('hybrid_coverage','hybrid/coverage/events','Duplicate event disposition.')
        ledger[key] = row
    wanted_keys = {(tid,*k) for tid in rows for k in rows[tid]}
    check.equal('hybrid_coverage','hybrid/coverage/events',sorted(wanted_keys),sorted(ledger))
    for key in wanted_keys & ledger.keys():
        row=ledger[key]; tid=key[0]
        original = rows[tid][key[1:]]
        location = f'hybrid/coverage/{tid}/{key[1]}/{key[2]}'
        targets = []
        if key in selected:
            status, reason = 'included', 'copied_source_event'
        elif roles[tid] == 'excluded':
            status, reason = 'excluded', 'explicit_user_choice'
        elif roles[tid] == 'main':
            status, reason = 'superseded', 'primary_solo'
            targets = sorted({t for t,a,b in regions if _touch((original['start'],original['end']),(a,b))})
        elif roles[tid] == 'solo':
            targets = displaced_solos.get(key,[])
            status, reason = ('superseded','preferred_solo') if targets else ('non_primary','outside_solo_body')
        elif roles[tid] == 'lead':
            status, reason = 'superseded', 'higher_primary'
            span = original['start'],original['end']
            targets = sorted({t for t,a,b in regions+lead_regions if t != tid and _touch(span,(a,b))}
                             | ({main_id} if any(_touch(span,p) for p in main_spans) else set()))
            if not targets:
                check.fail('hybrid_primary_coverage','hybrid/coverage','A primary lead event has no higher-priority replacement.')
        else:
            status, reason = 'unused_accompaniment', row.get('reason')
            allowed = {'phrase_plan_preference','no_supported_phrase_boundary','crosses_primary_material',
                       'transition_guard_or_small_gap','past_recording_end','incompatible_setup','effect_layer',
                       'duplicate_main','duplicate_source','source_omissions','no_complete_passage_fits','no_supported_passage'}
            if not reason or not set(reason.split(',')) <= allowed:
                check.fail('hybrid_coverage','hybrid/coverage/events','An unused accompaniment event has no supported disposition.')
        check.equal('hybrid_coverage','hybrid/coverage/event/status',status,row.get('status'))
        check.equal('hybrid_coverage','hybrid/coverage/event/reason',reason,row.get('reason'))
        check.equal('hybrid_coverage','hybrid/coverage/event/supersededBy',targets,row.get('supersededBy'))
        for field in ('start','end'):
            check.near('hybrid_coverage',location+'/'+field,original[field],row[field],1e-5)
            check.near('hybrid_coverage',location+'/recording'+field.title(),recording_at(original[field]),row['recording'+field.title()],2*TOL)
        for field in ('sourceIds','occurrences'):
            check.equal('hybrid_event_lineage','hybrid/coverage/'+field,original[field],row.get(field))
    return kept, removed, rows, protected
