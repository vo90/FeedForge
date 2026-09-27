"""Independent primary-role and regional coverage audits for Hybrid Lead.

This module never imports the producer's planner, context or materializer.
Written facts and directional technique edges come from the independent source
reader. Coverage is checked even when a forged output has no silence at all.
The v3 branch also establishes conservative named-owner/dedicated-solo
expectations without trusting the producer's role map or ranking diagnostics.
"""
from collections import Counter, defaultdict
from copy import deepcopy
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
            events[key] = {'start': quarter_at(min(starts)), 'end': quarter_at(max(ends)),
                           'onset': quarter_at(min(n['t'] for n in members)), 'notes': members,
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


def audit(charts, receipt, facts, options, quarter_at, recording_at, duration, source_format, check, *, source=None, musical_audit=None):
    if options.get('policy') == 'hybrid-lead-v3':
        return _audit_regional(charts, receipt, facts, options, quarter_at, recording_at, duration, source_format, check, source, musical_audit)
    return _audit_v2(charts, receipt, facts, options, quarter_at, recording_at, duration, source_format, check)


def _audit_v2(charts, receipt, facts, options, quarter_at, recording_at, duration, source_format, check):
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


def _words(value):
    return set(re.findall(r'[a-z]+|\d+', str(value).casefold()))


def _role_words(name):
    labels = {'guitar', 'lead', 'solo', 'rhythm', 'chord', 'chords', 'harmony', 'harmonies', 'double',
              'extra', 'extras', 'overdub', 'overdubs', 'background', 'delay', 'echo', 'effect', 'fx',
              'main', 'clean', 'acoustic', 'electric', 'classical'}
    fields = re.sub(r'\bguitars\b', 'guitar', str(name), flags=re.I).split('|')
    if len(fields) == 1:
        return _words(fields[0])
    result = set()
    for field in fields:
        words = _words(field) - {'and', 'part', 'i', 'ii', 'iii', 'iv'}
        words = {w for w in words if not w.isdecimal()}
        if 'guitar' in words or words and words <= labels:
            result.update(words & labels)
    return result


def _dedicated(name):
    words = _role_words(name)
    return 'solo' in words and not words & {'lead', 'rhythm', 'chord', 'chords', 'harmony', 'harmonies', 'double', 'delay', 'echo', 'effect', 'fx'}


def _closed_members(values, start, end):
    """Select attacks in the region, then close only hard source dependencies.

    A later independent backing attack under a held solo tail is not itself a
    dependency. Expanding over every overlapping sound would swallow the song.
    """
    members = {key for key, row in values.items() if start-EPS <= row.get('onset', row['start']) < end-EPS}
    spans = {(values[k]['start'], values[k]['end']) for k in members}
    return members | {k for k, r in values.items() if (r['start'], r['end']) in spans}


def _solo_islands(values):
    islands = []
    for start, end in _joined([(r['start'], r['end']) for r in values.values()], gap=1):
        local = {k: r for k, r in values.items() if start-EPS <= r['start'] and r['end'] <= end+EPS}
        body = [r for r in local.values() if any(not n.get('ghost') and not n.get('mt') for n in r['notes'])]
        if not body:
            continue
        members = _closed_members(local, min(r['start'] for r in body), max(r['end'] for r in body))
        if members:
            islands.append({'start': min(local[k]['start'] for k in members),
                            'end': max(local[k]['end'] for k in members), 'members': members})
    return islands


def _local_texture(values, start, end):
    """Conservative source facts for a contrast, never a lead-ranking score.

    The audit needs corroborating evidence before it rejects a named performer's
    otherwise credible voice. Register, note count and a track's rhythm label
    cannot establish that a part is backing. Plain, low-register and polyphonic
    solos remain possible; ambiguous voices stay alternatives.
    """
    attacks = defaultdict(list)
    for row in values.values():
        onset = row.get('onset', row['start'])
        if start-EPS <= onset < end-EPS:
            attacks[round(onset, 5)].extend(n for n in row['notes'] if not n.get('ghost') and not n.get('mt'))
    attacks = {time: notes for time, notes in attacks.items() if notes}
    notes = [n for members in attacks.values() for n in members]
    if not notes:
        return {'expressiveLine': False, 'plainLine': False, 'backingTexture': False}
    count = len(notes)
    signatures = Counter(tuple(sorted((n['s'], n['f']) for n in members)) for members in attacks.values())
    expressive = sum(any(n.get('bn') or n.get('vb') or n.get('whammy') for n in members) for members in attacks.values())
    changing = len({(n['s'], n['f']) for n in notes}) >= 4
    muted = sum(bool(n.get('pm')) for n in notes) / count
    ringing = sum(bool(n.get('lr')) for n in notes) / count
    chordal = sum(len(members) >= 2 for members in attacks.values()) / len(attacks)
    repeats = sum(n for n in signatures.values() if n >= 3) / len(attacks)
    # Recurrent chord shapes or a recurrent let-ring texture independently
    # corroborate backing. Neither a clean sound nor simple arpeggiation alone
    # is a veto; it must lose to a separately supported foreground line.
    backing = (len(attacks) >= 6 and expressive == 0 and repeats >= .6
               and (chordal >= .6 or ringing >= .6 or muted >= .75))
    return {'expressiveLine': expressive >= 2 and changing and muted < .5,
            'plainLine': changing and chordal <= .25 and muted < .5 and ringing < .5,
            'backingTexture': backing, 'chordalBacking': backing and chordal >= .6}


def _credible_named_sources(candidates, parts, rows, start, end, available=None):
    """Retain all credible alternatives unless a local contrast is decisive."""
    ordinary, secondary, effects = [], [], []
    for tid in candidates:
        words = _role_words(parts[tid]['source'].name)
        target = (effects if words & {'delay', 'echo', 'effect', 'fx'} else
                  secondary if words & {'harmony', 'harmonies', 'double', 'extra', 'extras', 'overdub', 'overdubs', 'background'}
                  else ordinary)
        target.append(tid)
    # Harmony can be the only tabbed rendition of a named solo, but an effect
    # layer alone is never independent evidence of the guitarist's foreground.
    texture = {tid: _local_texture(rows[tid], start, end) for tid in ordinary + secondary}
    eligible = ordinary or secondary
    # A source label such as Extra Lead can still contain the actual named
    # solo. Admit it only when every ordinary candidate is independently
    # established backing and the secondary voice has corroborating foreground
    # evidence. An ambiguous ordinary line keeps precedence over an overdub.
    # An unavailable ordinary source must not veto this playable alternative;
    # it still participates below so a genuinely unique unavailable solo cannot
    # disappear without its separate limitation disclosure.
    available_ordinary = [tid for tid in ordinary if available is None or tid in available]
    if available_ordinary and all(texture[tid]['backingTexture'] for tid in available_ordinary):
        eligible = ordinary + [tid for tid in secondary if (available is None or tid in available)
                               and (texture[tid]['expressiveLine'] or texture[tid]['plainLine']
                                    and all(texture[o].get('chordalBacking') for o in available_ordinary))]
    rejected = {tid for tid in eligible if texture[tid]['backingTexture'] and any(
        other != tid and (texture[other]['expressiveLine']
                         or texture[tid].get('chordalBacking') and texture[other]['plainLine'])
        for other in eligible)}
    return [tid for tid in eligible if tid not in rejected]


def regional_requirements(parts, rows, source, order, duration_quarters, *, available=None):
    """Conservative musical expectations, with no producer roles or candidates.

    This is deliberately not a second scoring algorithm. Unique named soloists
    and dedicated solo bodies establish requirements; parallel credible voices
    admit a complete coherent alternative. Unlabelled role inference is not
    falsely presented as independently proven musical truth.
    """
    from .verify_timeline import Clock
    clock = Clock(source, order)
    sections = [(float(clock.measure_starts[i]), str(source.bars[bi].section))
                for i, bi in enumerate(order) if source.bars[bi].section]
    sections.append((duration_quarters, ''))
    result = []
    for (start, label), (end, _) in zip(sections, sections[1:]):
        text = label.casefold()
        if not re.search(r'\bsolo\b', text) or re.search(r'\b(?:pre|post)[\s-]*solo\b', text):
            continue
        if re.search(r'\b(?:sax(?:ophone)?|tenor|bass|drum|keyboard|piano|organ|violin|vocal)\b', text):
            continue
        names = re.findall(r'\(([^()]*)\)', label)
        if not names:
            continue
        performers = [n.strip() for fragment in names for n in re.split(r'\s*(?:&|/|\band\b)\s*', fragment, flags=re.I) if n.strip()]
        credible = []
        for person in performers:
            wanted = _words(person) - {'guitar', 'solo', 'lead', 'part'}
            if not wanted:
                continue
            candidates = []
            for tid, part in parts.items():
                if tid not in rows or not rows[tid]:
                    continue
                name = part['source'].name
                # Names in the source are not assumed to have a fixed field
                # order. Instrument model words alone cannot establish a role.
                if not any(wanted <= _words(field) for field in name.split('|')):
                    continue
                local = {k: r for k, r in rows[tid].items() if _touch((r['start'], r['end']), (start, end))
                         and any(not n.get('ghost') and not n.get('mt') for n in r['notes'])}
                if not local:
                    continue
                candidates.append(tid)
            if candidates:
                credible.extend(_credible_named_sources(candidates, parts, rows, start, end, available))
        if credible:
            owners = {}
            for tid in sorted(set(credible)):
                body = [r for r in rows[tid].values() if start-EPS <= r['onset'] < min(end, duration_quarters)-EPS
                        and any(not n.get('ghost') and not n.get('mt') for n in r['notes'])]
                members = (_closed_members(rows[tid], min(r['onset'] for r in body),
                                           min(end, duration_quarters, max(r['end'] for r in body)))
                           if body else set())
                if members:
                    owners[tid] = members
            if owners:
                result.append({'evidence': 'named_soloist', 'start': start, 'end': min(end, duration_quarters),
                               'sectionName': label, 'owners': owners})
    dedicated = []
    for tid, part in parts.items():
        if tid not in rows or not _dedicated(part['source'].name):
            continue
        dedicated.extend({**island, 'trackId': tid} for island in _solo_islands(rows[tid]))
    # Parallel dedicated voices can differ in rhythm. Require one whole local
    # voice, but do not let a shorter parallel body waive another voice's unique
    # pickup or tail. Every independently closed gesture gets its own window.
    for region in dedicated:
        tid = region['trackId']
        windows = {(rows[tid][key]['start'], rows[tid][key]['end']) for key in region['members']}
        for lo, hi in sorted(windows):
            alternatives = {}
            for peer in dedicated:
                if peer['start'] > lo+EPS or peer['end'] < hi-EPS:
                    continue
                ident = peer['trackId']
                members = {key for key in peer['members']
                           if _touch((lo, hi), (rows[ident][key]['start'], rows[ident][key]['end']))}
                if members:
                    alternatives.setdefault(ident, set()).update(members)
            result.append({'evidence': 'dedicated_solo', 'start': lo, 'end': hi, 'owners': alternatives})
    return result


def _unsupported_groups(part, quarter_at, duration, source_format='songsterr'):
    """Local high-fret/slide evidence survives original-chart projection."""
    raw = deepcopy(part.get('sync_notes', part['notes']))
    bad = {i for i, row in enumerate(raw) if row['note']['t'] < duration-TOL
           and not row['note'].get('pick_scrape_marks')
           and any(type(row['note'].get(k)) is int and 24 < row['note'][k] <= 48 for k in ('f', 'sl', 'slu'))}
    if not bad:
        return []
    # The original-chart omission oracle legitimately clears incoming links.
    # Recover their independent written origins before closing the raw gesture.
    origins = {atom.location for bar in part['source'].bars for atom in bar if atom.hopo_origin or atom.slide == 'legato'}
    for row in raw:
        if origins & set(row['locations']):
            row['note']['ln'] = True
    # Reconstruct authored simultaneous chords independently. Flattening their
    # atoms loses the connection between one linked string and the other chord
    # members, and can incorrectly shrink an unsupported whole gesture.
    buckets = defaultdict(list)
    trill_beats = {(r['occurrence'], r['beat']) for r in raw if r.get('trill')}
    for index, row in enumerate(raw):
        beat = row['occurrence'], row['beat']
        buckets[(*beat, row['note']['t'] if beat in trill_beats else None)].append(index)
    raw_chart, bad_keys = {'notes': [], 'chords': []}, set()
    for indices in buckets.values():
        notes = [raw[i]['note'] for i in indices]
        if len(notes) > 1 and len({n['t'] for n in notes}) == 1:
            key = 'chords', len(raw_chart['chords'])
            raw_chart['chords'].append({'t': notes[0]['t'], 'notes': [{k: v for k, v in n.items() if k != 't'} for n in notes]})
            if bad & set(indices):
                bad_keys.add(key)
        else:
            for index in indices:
                key = 'notes', len(raw_chart['notes'])
                raw_chart['notes'].append(raw[index]['note'])
                if index in bad:
                    bad_keys.add(key)
    values = _events(raw_chart, {**part, 'notes': raw}, quarter_at, duration, source_format)
    spans = {(values[key]['start'], values[key]['end']) for key in bad_keys}
    groups = []
    for lo, hi in sorted(spans):
        members = [r for r in values.values() if (r['start'], r['end']) == (lo, hi)]
        groups.append({'start': lo, 'end': hi, 'sourceIds': {sid for r in members for sid in r['sourceIds']},
                       'occurrences': {o for r in members for o in r['occurrences']},
                       'events': members})
    return groups


def _unsupported_regions(part, quarter_at, duration, source_format='songsterr'):
    return _joined([(g['start'], g['end']) for g in _unsupported_groups(part, quarter_at, duration, source_format)])


def _audit_regional(charts, receipt, facts, options, quarter_at, recording_at, duration, source_format, check, source, musical_audit=None):
    main_id = options['mainTrackId']
    parts = {p['source'].id: p for p in facts['parts'] if p['source'].instrument == 'guitar'}
    rows = {tid: _events(chart, parts[tid], quarter_at, duration, source_format)
            for tid, chart in charts.items() if tid in parts}
    for tid in parts:
        rows.setdefault(tid, {})
    if source is None:
        check.fail('hybrid_regional_source', 'hybrid', 'Independent regional verification requires the retained source.')
        return set(), set(), rows, []
    main = charts[main_id]
    # Source tuning contains physical MIDI pitches; chart tuning contains
    # offsets from the instrument's standard tuning. Compare like with like.
    base_source = parts[main_id]['source']
    compatible = {tid for tid in parts if list(parts[tid]['source'].tuning) == list(base_source.tuning)
                  and parts[tid]['source'].capo == base_source.capo}
    excluded = set(options.get('excludedTrackIds', []))
    kept = {(r['kind'], r['index']) for r in receipt.get('mainEvents', [])}
    removed = {(r['kind'], r['index']) for r in receipt.get('removedMain', [])}
    if len(kept) != len(receipt.get('mainEvents', [])) or len(removed) != len(receipt.get('removedMain', [])):
        check.fail('hybrid_main_coverage', 'hybrid/mainEvents', 'Duplicate main event reference.')
    check.equal('hybrid_main_coverage', 'hybrid/mainEvents', sorted(rows[main_id]), sorted(kept | removed))
    check.equal('hybrid_main_coverage', 'hybrid/removedMain', [], sorted(kept & removed))
    valid_kept, valid_removed = kept & rows[main_id].keys(), removed & rows[main_id].keys()
    primary = [p for p in receipt.get('passages', []) if p.get('priority') in {'solo', 'lead'}]
    selected = {(main_id, *key) for key in valid_kept}
    for passage in receipt.get('passages', []):
        tid = passage['trackId']
        if tid not in compatible or tid in excluded:
            check.fail('hybrid_primary_setup', 'hybrid/passages', 'A selected source violates the fixed base setup or explicit exclusion.')
        if passage.get('priority') not in {'solo', 'lead', 'accompaniment'}:
            check.fail('hybrid_primary_priority', 'hybrid/passages', 'Unknown passage priority.')
        for ref in passage['events']:
            selected.add((tid, ref['kind'], ref['index']))
        if passage in primary:
            members = [rows.get(tid, {}).get((r['kind'], r['index'])) for r in passage['events']]
            if not members or any(r is None for r in members):
                check.fail('hybrid_primary_coverage', 'hybrid/passages', 'Unknown or empty primary passage.')
                continue
            check.near('hybrid_primary_boundary', 'hybrid/passages/start', min(r['start'] for r in members), passage['start'], 1e-5)
            check.near('hybrid_primary_boundary', 'hybrid/passages/end', max(r['end'] for r in members), passage['end'], 1e-5)
            check.equal('hybrid_primary_boundary', 'hybrid/passages/boundaries', ['primary', 'primary'], passage.get('boundaries'))
            check.equal('hybrid_primary_boundary', 'hybrid/passages/boundaryQuarters', [passage['start'], passage['end']], passage.get('boundaryQuarters'))
    for ref in receipt.get('mainEvents', []) + receipt.get('removedMain', []):
        original = rows[main_id].get((ref['kind'], ref['index']))
        if original:
            for field in ('sourceIds', 'occurrences'):
                check.equal('hybrid_event_lineage', 'hybrid/mainEvents/'+field, original[field], ref.get(field))
    # A primary replacement must justify every removed whole main gesture.
    for key in valid_removed:
        row = rows[main_id][key]
        if not any(_touch((row['start'], row['end']), (p['start'], p['end'])) for p in primary):
            check.fail('hybrid_main_coverage', 'hybrid/removedMain', 'A main gesture disappeared without a primary replacement.')
    for ref in receipt.get('removedMain', []):
        row = rows[main_id].get((ref['kind'], ref['index']))
        if row:
            for field in ('start', 'end'):
                check.near('hybrid_main_coverage', 'hybrid/removedMain/'+field, row[field], ref.get(field), 1e-5)
                check.near('hybrid_main_coverage', 'hybrid/removedMain/recording'+field.title(), recording_at(row[field]), ref.get('recording'+field.title()), 2*TOL)
            targets = sorted({p['trackId'] for p in primary if _touch((row['start'], row['end']), (p['start'], p['end']))})
            check.equal('hybrid_main_coverage', 'hybrid/removedMain/supersededBy', targets, ref.get('supersededBy'))
    for tid, values in rows.items():
        groups = defaultdict(set)
        for key, row in values.items():
            groups[(row['start'], row['end'])].add((tid, *key))
        for members in groups.values():
            if members & selected and not members <= selected:
                check.fail('hybrid_primary_gesture', 'hybrid/events', 'Only part of an independently connected source gesture was retained.')

    available = {tid for tid in compatible-excluded if options.get('roles', {}).get(tid) != 'accompaniment'}
    requirements = regional_requirements(parts, rows, source, facts['order'], quarter_at(duration), available=available)
    if musical_audit is not None:
        musical_audit.update({
            'scope': 'named_soloists_and_dedicated_solo_bodies',
            'requirementCountScope': 'retained_playable_source_events',
            'requirementCount': len(requirements),
            'uniqueOwnerCount': sum(len(r['owners']) == 1 for r in requirements),
            'alternativeVoiceCount': sum(len(r['owners']) > 1 for r in requirements),
            'namedSectionCount': sum(r['evidence'] == 'named_soloist' for r in requirements),
            'unverifiedClaim': 'Unlabelled musical choices and listening quality are not established by this audit.',
        })
    def mandated_hard_conflict(tid, key):
        """Prove a local impossible handover under whole-gesture composition.

        A receipt priority or a busy backing part is not evidence. A uniquely
        named owner needs another adjacent named owner. A dedicated gesture can
        also conflict with another independently required foreground component.
        """
        here = rows[tid][key]
        origins = [r for r in requirements if r['evidence'] == 'named_soloist'
                   and set(r['owners']) == {tid} and key in r['owners'][tid]]
        for origin in origins:
            for other in requirements:
                if other['evidence'] != 'named_soloist' or len(other['owners']) != 1:
                    continue
                other_id = next(iter(other['owners']))
                adjacent = abs(origin['end']-other['start']) <= EPS or abs(other['end']-origin['start']) <= EPS
                if other_id == tid or not adjacent or _touch((origin['start'], origin['end']), (other['start'], other['end'])):
                    continue
                for other_key in other['owners'][other_id]:
                    rival = rows[other_id][other_key]
                    if not _touch((here['start'], here['end']), (rival['start'], rival['end'])):
                        continue
                    component = {(other_id, *k) for k, row in rows[other_id].items()
                                 if (row['start'], row['end']) == (rival['start'], rival['end'])}
                    if component and component <= selected:
                        return True
        if not origins and any(r['evidence'] == 'dedicated_solo' and key in r['owners'].get(tid, set()) for r in requirements):
            for other in requirements:
                if other['evidence'] == 'named_soloist' and len(other['owners']) != 1:
                    continue
                for other_id, members in other['owners'].items():
                    if other_id == tid:
                        continue
                    for other_key in members:
                        rival = rows[other_id][other_key]
                        if not _touch((here['start'], here['end']), (rival['start'], rival['end'])):
                            continue
                        component = {(other_id, *k) for k, row in rows[other_id].items()
                                     if (row['start'], row['end']) == (rival['start'], rival['end'])}
                        if component and component <= selected:
                            return True
        return False
    def retained_base_conflict(tid, lo, hi):
        """A physical conflict can explain an omitted inferred passage only.

        A retained base is not automatically an independent musical obligation.
        Never let its occupancy excuse any named or dedicated source gesture.
        The receipt's primaryEpisodes and priority labels are not evidence.
        """
        if tid == main_id:
            return False
        local = {key: row for key, row in rows[tid].items()
                 if lo-EPS <= row['start'] and row['end'] <= hi+EPS}
        if (not local or abs(min(row['start'] for row in local.values())-lo) > EPS
                or abs(max(row['end'] for row in local.values())-hi) > EPS
                or any(set(local) & requirement['owners'].get(tid, set()) for requirement in requirements)):
            return False
        return any(_touch((row['start'], row['end']), (rows[main_id][key]['start'], rows[main_id][key]['end']))
                   for row in local.values() for key in valid_kept)
    alternative_islands = {}
    def alternate_selected(tid, lo, hi, *, dedicated_only=False):
        # A simultaneous alternative can never erase a uniquely named owner.
        if any(r['evidence'] == 'named_soloist' and len(r['owners']) == 1 and tid in r['owners']
               and _touch((lo, hi), (r['start'], r['end'])) for r in requirements):
            return False
        def credible(ident):
            if options.get('roles', {}).get(ident) in {'lead', 'solo'}:
                return True  # Request-bound musical intent, for alternatives only.
            words = _role_words(parts[ident]['source'].name)
            return bool(words & {'lead', 'solo'}) and not words & {'chord', 'chords', 'background', 'delay', 'echo', 'effect', 'fx'}
        if not credible(tid):
            return False
        if dedicated_only or _dedicated(parts[tid]['source'].name):
            required = {key for key, row in rows[tid].items()
                        if _touch((lo, hi), (row['start'], row['end']))}
            missing = {key for key in required if (tid, *key) not in selected}
            for key in missing:
                row = rows[tid][key]
                found = False
                for other in rows:
                    if other == tid or other not in compatible or not credible(other):
                        continue
                    if dedicated_only and not (_dedicated(parts[other]['source'].name) or options.get('roles', {}).get(other) == 'solo'):
                        continue
                    if other not in alternative_islands:
                        alternative_islands[other] = _solo_islands(rows[other])
                    for island in alternative_islands[other]:
                        if island['start'] > row['start']+EPS or island['end'] < row['end']-EPS:
                            continue
                        members = {k for k in island['members']
                                   if _touch((row['start'], row['end']), (rows[other][k]['start'], rows[other][k]['end']))}
                        if members and {(other, *k) for k in members} <= selected:
                            found = True
                            break
                    if found:
                        break
                if not found:
                    return False
            return bool(required)
        for other in rows:
            if other == tid or other not in compatible or not credible(other):
                continue
            if dedicated_only and not (_dedicated(parts[other]['source'].name) or options.get('roles', {}).get(other) == 'solo'):
                continue
            members = _closed_members(rows[other], lo, hi)
            if members and {(other, *key) for key in members} <= selected:
                return True
        return False
    unsupported_groups = {tid: _unsupported_groups(part, quarter_at, duration, source_format) for tid, part in parts.items()}
    unsupported = {tid: _joined([(g['start'], g['end']) for g in groups]) for tid, groups in unsupported_groups.items()}
    # Projection can remove every event of a named solo, particularly when its
    # source is labelled rhythm. Restore independently proven raw components
    # for a disclosure-only pass. Never demand that impossible notes be played.
    raw_rows = {tid: dict(values) for tid, values in rows.items()}
    for tid, groups in unsupported_groups.items():
        for index, group in enumerate(groups):
            raw_rows[tid] = {key: row for key, row in raw_rows[tid].items()
                             if not (set(row.get('sourceIds', [])) & group['sourceIds']
                                     and set(row.get('occurrences', [])) & group['occurrences'])}
            for event_index, event in enumerate(group['events']):
                raw_rows[tid][('unsupported', index, event_index)] = event
    raw_requirements = regional_requirements(parts, raw_rows, source, facts['order'], quarter_at(duration), available=available)
    if musical_audit is not None:
        musical_audit['unsupportedNamedRequirementCount'] = sum(
            requirement['evidence'] == 'named_soloist'
            and any(key[0] == 'unsupported' for members in requirement['owners'].values() for key in members)
            for requirement in raw_requirements)
    limits = receipt.get('limitations', [])
    valid_limits = []
    for limit in limits:
        tid, lo, hi, reason = limit.get('trackId'), limit.get('start'), limit.get('end'), limit.get('reason')
        if tid not in parts or type(lo) not in {int, float} or type(hi) not in {int, float} or not math.isfinite(lo) or not math.isfinite(hi) or hi < lo:
            check.fail('hybrid_regional_limitation', 'hybrid/limitations', 'Invalid limitation identity or bounds.')
            continue
        valid = (reason == 'incompatible_setup' and tid not in compatible
                 or reason == 'excluded_by_user' and tid in excluded
                 or reason == 'explicit_accompaniment' and options.get('roles', {}).get(tid) == 'accompaniment'
                 or reason == 'unsupported_source_gesture' and any(a-EPS <= lo and hi <= b+EPS for a, b in unsupported[tid])
                 or reason == 'planning_limit')
        if reason == 'conflicting_primary':
            # This explains a real overlapping selected primary, not arbitrary
            # rhythm occupancy. Unique named-owner requirements below remain
            # mandatory and cannot be cancelled by this generic diagnostic.
            valid = (any(p['trackId'] != tid and _touch((lo, hi), (p['start'], p['end'])) for p in primary)
                     or any(lo-EPS <= r['start'] and r['end'] <= hi+EPS and mandated_hard_conflict(tid, key)
                            for key, r in rows[tid].items())
                     or retained_base_conflict(tid, lo, hi))
        elif reason == 'alternate_voice':
            valid = alternate_selected(tid, lo, hi)
        if not valid:
            check.fail('hybrid_regional_limitation', 'hybrid/limitations', 'A limitation has no independent source or request evidence.')
        else:
            valid_limits.append(limit)
    def explained(tid, keys, *, allow_conflict=False):
        reasons = {'incompatible_setup', 'excluded_by_user', 'explicit_accompaniment', 'unsupported_source_gesture', 'conflicting_primary'}
        if allow_conflict:
            reasons.update({'conflicting_primary', 'alternate_voice'})
        def covers(limit, key):
            row = rows[tid][key]
            if (limit['trackId'] != tid or limit['reason'] not in reasons
                    or limit['start']-EPS > row['start'] or row['end'] > limit['end']+EPS):
                return False
            if limit['reason'] == 'conflicting_primary' and not allow_conflict:
                return mandated_hard_conflict(tid, key)
            if limit['reason'] != 'unsupported_source_gesture':
                return True
            return any(g['sourceIds'] & set(row['sourceIds']) and g['occurrences'] & set(row['occurrences'])
                       and limit['start']-EPS <= g['start'] and g['end'] <= limit['end']+EPS
                       for g in unsupported_groups[tid])
        return all(any(covers(l, key) for l in valid_limits) for key in keys)
    known_missing = any(l['reason'] != 'alternate_voice' for l in valid_limits)
    for requirement in raw_requirements:
        if requirement['evidence'] != 'named_soloist':
            continue
        # A fully selected credible parallel voice satisfies the local musical
        # requirement. Otherwise each omitted raw owner needs a real local
        # setup/request/unsupported explanation, not a blanket completeness claim.
        if any({(tid, *key) for key in members} <= selected for tid, members in requirement['owners'].items()):
            continue
        raw_owners = {tid: [raw_rows[tid][key] for key in members if key[0] == 'unsupported']
                      for tid, members in requirement['owners'].items()}
        raw_owners = {tid: groups for tid, groups in raw_owners.items() if groups}
        if not raw_owners:
            continue
        known_missing = True
        if not any(all(any(limit['trackId'] == tid and limit['reason'] in {
                        'incompatible_setup', 'excluded_by_user', 'explicit_accompaniment', 'unsupported_source_gesture'}
                        and limit['start']-EPS <= group['start'] and group['end'] <= limit['end']+EPS
                        for limit in valid_limits) for group in groups) for tid, groups in raw_owners.items()):
            check.fail('hybrid_regional_limitation', 'hybrid/limitations',
                       'Unsupported named-solo source material was not disclosed locally.',
                       {'owners': sorted(raw_owners), 'start': requirement['start'], 'end': requirement['end']})
    for requirement in requirements:
        satisfied = [(tid, members) for tid, members in requirement['owners'].items()
                     if {(tid, *key) for key in members} <= selected]
        if (not satisfied and requirement['evidence'] == 'dedicated_solo'
                and any(alternate_selected(tid, requirement['start'], requirement['end'], dedicated_only=True) for tid in requirement['owners'])):
            satisfied = True
        if satisfied:
            continue
        known_missing = True
        if not any(explained(tid, {k for k in members if (tid, *k) not in selected})
                   for tid, members in requirement['owners'].items()):
            check.fail('hybrid_primary_coverage', 'hybrid/obligations',
                       'An independently identified solo is absent or replaced by the wrong guitarist.',
                       {'evidence': requirement['evidence'], 'owners': sorted(requirement['owners']),
                        'start': requirement['start'], 'end': requirement['end']})

    obligation_ids = set()
    for obligation in receipt.get('obligations', []):
        tid, status = obligation.get('trackId'), obligation.get('status')
        if obligation.get('id') in obligation_ids:
            check.fail('hybrid_regional_obligation', 'hybrid/obligations', 'Duplicate obligation identity.')
        obligation_ids.add(obligation.get('id'))
        refs = {(r['kind'], r['index']) for r in obligation.get('events', [])}
        if tid not in rows or status not in {'included', 'limited', 'excluded'}:
            check.fail('hybrid_regional_obligation', 'hybrid/obligations', 'Unknown obligation source or status.')
            continue
        lo, hi = obligation.get('start'), obligation.get('end')
        if (type(lo) not in {int, float} or type(hi) not in {int, float} or not math.isfinite(lo) or not math.isfinite(hi)
                or lo < -EPS or hi <= lo or hi > quarter_at(duration)+EPS):
            check.fail('hybrid_regional_obligation', 'hybrid/obligations', 'An obligation has invalid score bounds.')
            continue
        if len(refs) != len(obligation.get('events', [])) or not refs <= rows[tid].keys():
            check.fail('hybrid_regional_obligation', 'hybrid/obligations', 'Duplicate or unknown obligation event.')
            continue
        if status == 'included' and not refs:
            check.fail('hybrid_regional_obligation', 'hybrid/obligations', 'An included obligation has no source events.')
        if any(rows[tid][key]['start'] < lo-EPS or rows[tid][key]['end'] > hi+EPS for key in refs):
            check.fail('hybrid_regional_obligation', 'hybrid/obligations', 'An obligation event lies outside its complete source footprint.')
        for ref in obligation.get('events', []):
            for field in ('sourceIds', 'occurrences'):
                check.equal('hybrid_event_lineage', 'hybrid/obligations/'+field, rows[tid][(ref['kind'], ref['index'])][field], ref.get(field))
        missing = {key for key in refs if (tid, *key) not in selected}
        if status == 'included' and missing:
            check.fail('hybrid_primary_coverage', 'hybrid/obligations', 'An included obligation has missing source events.')
        if status != 'included':
            accepted_alternative = obligation.get('reason') == 'alternate_voice' and alternate_selected(tid, obligation['start'], obligation['end'])
            known_missing |= not accepted_alternative
            if missing and not explained(tid, missing, allow_conflict=True):
                check.fail('hybrid_regional_limitation', 'hybrid/obligations', 'An omitted obligation is not explained by a supported limitation.')
    # Raw unsupported material must be disclosed even when chart projection
    # gives it no event index. It cannot vanish from the completeness claim.
    for tid, spans in unsupported.items():
        if not spans:
            continue
        if any(r['evidence'] == 'dedicated_solo' and tid in r['owners'] for r in requirements) or _dedicated(parts[tid]['source'].name):
            known_missing = True
            for lo, hi in spans:
                declared_spans = _joined([(l['start'], l['end']) for l in valid_limits if l['trackId'] == tid
                                          and l['reason'] in {'incompatible_setup', 'excluded_by_user', 'explicit_accompaniment', 'unsupported_source_gesture'}])
                if not any(a-EPS <= lo and hi <= b+EPS for a, b in declared_spans):
                    check.fail('hybrid_regional_limitation', 'hybrid/limitations', 'Unsupported primary source material was not disclosed locally.')
    expected_status = 'limited' if known_missing else 'complete'
    check.equal('hybrid_coverage', 'hybrid/coverage/status', expected_status, receipt.get('coverage', {}).get('status'))

    ledger = {}
    for row in receipt.get('coverage', {}).get('events', []):
        if row.get('index') is None and row.get('reason') == 'recording_end':
            continue
        key = row['trackId'], row['kind'], row['index']
        if key in ledger:
            check.fail('hybrid_coverage', 'hybrid/coverage/events', 'Duplicate event disposition.')
        ledger[key] = row
    wanted = {(tid, *key) for tid, values in rows.items() for key in values}
    check.equal('hybrid_coverage', 'hybrid/coverage/events', sorted(wanted), sorted(ledger))
    for key in wanted & ledger.keys():
        original, row = rows[key[0]][key[1:]], ledger[key]
        if key in selected:
            check.equal('hybrid_coverage', 'hybrid/coverage/event/status', 'included', row.get('status'))
            check.equal('hybrid_coverage', 'hybrid/coverage/event/reason', 'copied_source_event', row.get('reason'))
        elif row.get('status') == 'included':
            check.fail('hybrid_coverage', 'hybrid/coverage/events', 'An unselected source event claims inclusion.')
        elif key[0] == main_id:
            check.equal('hybrid_coverage', 'hybrid/coverage/event/status', 'superseded', row.get('status'))
            check.equal('hybrid_coverage', 'hybrid/coverage/event/reason', 'regional_primary', row.get('reason'))
            targets = sorted({p['trackId'] for p in primary if _touch((original['start'], original['end']), (p['start'], p['end']))})
            check.equal('hybrid_coverage', 'hybrid/coverage/event/supersededBy', targets, row.get('supersededBy'))
        elif key[0] not in compatible or key[0] in excluded:
            check.equal('hybrid_coverage', 'hybrid/coverage/event/status', 'excluded', row.get('status'))
            allowed_reasons = ({'incompatible_setup'} if key[0] not in compatible else set()) | ({'excluded_by_user'} if key[0] in excluded else set())
            if row.get('reason') not in allowed_reasons:
                check.fail('hybrid_coverage', 'hybrid/coverage/events', 'An excluded source has an incorrect setup/request reason.')
        else:
            allowed = {'phrase_plan_preference', 'no_supported_phrase_boundary', 'crosses_primary_material',
                       'transition_guard_or_small_gap', 'past_recording_end', 'effect_layer', 'duplicate_main',
                       'duplicate_source', 'source_omissions', 'no_complete_passage_fits', 'no_supported_passage',
                       'outside_solo_body', 'explicit_accompaniment', 'regional_primary', 'alternate_voice',
                       'conflicting_primary', 'unsupported_source_gesture', 'planning_limit'}
            if (row.get('status') not in {'unused_accompaniment', 'non_primary', 'superseded', 'source_limitation'}
                    or not row.get('reason') or not set(row['reason'].split(',')) <= allowed):
                check.fail('hybrid_coverage', 'hybrid/coverage/events', 'An unselected event has no supported disposition.')
        for field in ('start', 'end'):
            check.near('hybrid_coverage', 'hybrid/coverage/'+field, original[field], row.get(field), 1e-5)
            check.near('hybrid_coverage', 'hybrid/coverage/recording'+field.title(), recording_at(original[field]), row.get('recording'+field.title()), 2*TOL)
        for field in ('sourceIds', 'occurrences'):
            check.equal('hybrid_event_lineage', 'hybrid/coverage/'+field, original[field], row.get(field))
    slots = [(quarter_at(float(b['time'])), quarter_at(min(duration, float(b['end']))))
             for b in parts[main_id]['notation_beats'] if not b['rest'] and float(b['time']) < duration]
    slots = [span for span in slots if not any(_touch(span, (rows[main_id][k]['start'], rows[main_id][k]['end'])) for k in valid_removed)]
    protected = _joined(slots + [(rows[main_id][k]['start'], rows[main_id][k]['end']) for k in valid_kept]
                        + [(p['start'], p['end']) for p in primary])
    return set(valid_kept), set(valid_removed), rows, protected
