"""Conservative, deterministic whole-passage composition in musical time.

No notes are generated, retimed, shortened, or re-fingered here. The output is a
plan referencing original performed events; materialization happens separately.
"""
from copy import deepcopy
import json
import re
from statistics import median

from .audio import ImportFailure
from .alignment import map_time
from .hybrid_context import Clock

POLICY = "hybrid-lead-v3"
NAME = "Hybrid Lead"
EPS = 1e-7
MAX_CANDIDATES = 20000
MAX_STATES = 10000
MAX_OPERATIONS = 500000


def normalize_options(value=None):
    if value is None:
        return {"enabled": False}
    if not isinstance(value, dict) or not isinstance(value.get("enabled", False), bool):
        raise ValueError("Hybrid Lead options must include a boolean enabled setting.")
    if set(value) - {"enabled", "policy", "roles", "mainTrackId", "excludedTrackIds", "preferredTrackIds", "sourceSha256", "reviewSources"}:
        raise ValueError("Unknown Hybrid Lead option.")
    if not value.get("enabled"):
        return {"enabled": False}
    if value.get("policy", POLICY) != POLICY:
        raise ValueError("This Hybrid Lead request uses an older musical policy. Review its sources again.")
    out = {"enabled": True, "policy": POLICY}
    roles = value.get("roles", {})
    if not isinstance(roles, dict) or len(roles) > 128 or any(not isinstance(k, str) or not 0 < len(k) <= 160 or v not in {"solo", "lead", "accompaniment"} for k, v in roles.items()):
        raise ValueError("Invalid Hybrid Lead source roles.")
    out["roles"] = dict(sorted(roles.items()))
    for key in ("mainTrackId", "sourceSha256"):
        if key in value:
            if not isinstance(value[key], str) or not 0 < len(value[key]) <= 160:
                raise ValueError("Invalid Hybrid Lead source identity.")
            out[key] = value[key]
    for key in ("excludedTrackIds", "preferredTrackIds"):
        items = value.get(key, [])
        if not isinstance(items, list) or len(items) > 128 or any(not isinstance(x, str) or not 0 < len(x) <= 160 for x in items):
            raise ValueError("Invalid Hybrid Lead source selection.")
        out[key] = list(dict.fromkeys(items))
    if value.get("reviewSources") is True:
        out["reviewSources"] = True
    return out


def choose_main(performance, options, source_hash):
    if not options["enabled"]:
        return None
    from .high_frets import project
    playable, _ = project(performance)
    guitars = [t for t in playable["tracks"] if t["instrument"] == "guitar" and (t["notes"] or t["chords"])]
    if not guitars:
        return None
    ids = {t['id'] for t in performance['tracks'] if t['instrument'] == 'guitar' and (t['notes'] or t['chords'])}
    if options.get("sourceSha256") and options["sourceSha256"] != source_hash:
        raise ImportFailure("hybrid_choice_stale", "The tab changed. Choose the main guitar again.")
    if (set(options.get("excludedTrackIds", [])) | set(options.get("preferredTrackIds", [])) | set(options.get("roles", {}))) - ids:
        raise ImportFailure("hybrid_choice_stale", "A selected guitar is no longer available in this tab.")
    if options.get("mainTrackId") and options["mainTrackId"] not in {t['id'] for t in guitars}:
        raise ImportFailure("hybrid_choice_stale", "The chosen main guitar is no longer available.")
    from .hybrid_selection import select_base
    selection = select_base(performance, options)
    performance['hybridBaseSelection'] = deepcopy(selection)
    suggested = selection['mainTrackId']
    if not options.get("reviewSources"):
        return suggested
    from .hybrid_primary import suggested_role
    raise ImportFailure("awaiting_main_choice", "Choose the guitar that Hybrid Lead should follow.", {
        "sourceSha256": source_hash, "suggestedMainTrackId": suggested,
        'suggestedRoles': {t['id']: suggested_role(t) or '' for t in performance['tracks'] if t['id'] in ids},
        "tracks": [{**{k: t[k] for k in ("id", "name", "role", "tuning", "capo")}, 'playable': any(g['id'] == t['id'] for g in guitars)}
                   for t in performance['tracks'] if t['id'] in ids]})


def union(intervals):
    out = []
    for a, b in sorted((a, b) for a, b in intervals):
        if out and a <= out[-1][1] + EPS:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def event_rows(track, context, clock):
    """Group source slots with their performed events, including technique tails."""
    beats = context["beats"]
    by_id = {}
    for b in beats:
        for ident in [b["sourceId"], *b["noteIds"]]:
            by_id.setdefault(ident, []).append(b)
    rows = []
    for kind in ("notes", "chords"):
        for index, event in enumerate(track[kind]):
            notes = [event] if kind == "notes" else [{"t": event["t"], **n} for n in event["notes"]]
            start = min(clock.quarter(n["t"]) for n in notes)
            end = max(clock.quarter(n["t"] + n.get("sus", 0)) for n in notes)
            ids = sorted(set(event.get("source_ids", []) + [sid for n in notes for sid in n.get("source_ids", [])]))
            selected = []
            # Ties span several source IDs. Choose each repeated ID's occurrence
            # within this event, never every occurrence of a repeated measure.
            for ident in ids:
                matches = by_id.get(ident, [])
                inside = [b for b in matches if start - EPS <= b["start"] < end - EPS]
                if inside:
                    selected.extend(inside)
                elif matches:
                    near = min(matches, key=lambda b: abs(b["start"] - start))
                    selected.append(near)
            if selected:
                start = min(start, *(b["start"] for b in selected))
                end = max(end, *(b["end"] for b in selected))
            rows.append({"kind": kind, "index": index, "start": start, "end": end,
                         "sourceIds": ids, "occurrences": sorted({b["occurrence"] for b in selected}),
                         "notes": notes})
    grace_groups = []
    for grace in (b for b in beats if b["grace"]):
        following = next((b for b in beats if b["voice"] == grace["voice"] and not b["rest"] and not b["grace"] and b["start"] >= grace["end"] - EPS), None)
        if following:
            linked = set(grace["noteIds"] + following["noteIds"])
            touched = [r for r in rows if linked.intersection(r["sourceIds"]) and r["start"] < following["end"] + EPS and r["end"] > grace["start"] - EPS]
            grace_groups.append([i for i,r in enumerate(rows) if any(r is item for item in touched)])
            for row in touched:
                row["start"] = min(row["start"], grace["start"])
                row["end"] = max(row["end"], following["end"])
    # ln belongs to the origin and points FORWARD. Build complete connected
    # components without mutating times while discovering subsequent edges.
    # Source voices remain distinct, even when they use the same string.
    parent = list(range(len(rows)))
    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for group in grace_groups:
        for i in group[1:]:
            parent[root(i)] = root(group[0])
    per_string = {}
    atoms = sorted(((n["t"], i, n) for i, row in enumerate(rows) for n in row["notes"]), key=lambda x: (x[0], x[1], x[2]["s"]))
    for _, i, note in atoms:
        ids = note.get("source_ids", [])
        voices = {b["voice"] for sid in ids for b in by_id.get(sid, [])}
        voice = tuple(sorted(voices)) or (0,)
        key = (voice, note["s"])
        prior = per_string.get(key)
        if prior and prior[0] != i:
            j, previous = prior
            if previous.get("ln") or previous.get("sl") is not None or previous.get("slu") or note.get("ho") or note.get("po"):
                parent[root(i)] = root(j)
        per_string[key] = i, note
    spans = {}
    for i, row in enumerate(rows):
        span = spans.setdefault(root(i), [row["start"], row["end"]])
        span[:] = min(span[0], row["start"]), max(span[1], row["end"])
    for i, row in enumerate(rows):
        row["start"], row["end"] = spans[root(i)]
    return sorted(rows, key=lambda r: (r["start"], r["kind"], r["index"]))


def occupied(track, context, clock):
    return union([(b["start"], b["end"]) for b in context["beats"] if not b["rest"]] +
                 [(r["start"], r["end"]) for r in event_rows(track, context, clock)])


def _fingerprint(rows, lo, hi, clock):
    values = []
    for r in rows:
        if r["end"] <= lo + EPS or r["start"] >= hi - EPS:
            continue
        if r["start"] < lo - EPS or r["end"] > hi + EPS:
            return None
        notes = []
        for n in r["notes"]:
            value = {k: v for k, v in n.items() if k not in {"t", "sus", "source_ids"}}
            value.update(q=round(clock.quarter(n["t"]) - lo, 8), length=round(clock.quarter(n["t"] + n.get("sus", 0)) - clock.quarter(n["t"]), 8))
            notes.append(value)
        values.append([r["kind"], round(r["start"] - lo, 8), round(r["end"] - lo, 8), notes])
    return json.dumps(values, sort_keys=True, separators=(",", ":")) if values else None


def _hand_endpoints(rows):
    notes = [n for row in rows for n in row['notes']]
    core = [n for n in notes if not n.get('ghost') and not n.get('mt')]
    notes = core or notes
    if not notes:
        return {'entryFret': None, 'exitFret': None}
    def position(onset):
        frets = [n['f'] for n in notes if abs(n['t'] - onset) < EPS and 0 < n['f'] <= 24]
        return median(frets) if frets else None
    return {'entryFret': position(min(n['t'] for n in notes)),
            'exitFret': position(max(n['t'] for n in notes))}


def _fixed_events(primary, main, context, clock):
    """Exact kept event positions around optional gaps, including solo donors."""
    rows = primary.get('rows') if primary else None
    if rows is None:
        rows = {main['id']: event_rows(main, context['tracks'][main['id']], clock)}
        selected = {(main['id'], row['kind'], row['index']) for row in rows[main['id']]}
    else:
        selected = {(main['id'], ref['kind'], ref['index']) for ref in primary['mainEvents']}
        selected.update((p['trackId'], ref['kind'], ref['index']) for p in primary['passages'] for ref in p['events'])
    groups = {}
    for tid, events in rows.items():
        for row in events:
            if (tid, row['kind'], row['index']) in selected:
                groups.setdefault((tid, row['start'], row['end']), []).append(row)
    return [{'trackId': tid, 'start': start, 'end': end, **_hand_endpoints(events)}
            for (tid, start, end), events in groups.items()]


def passages(track, context, timeline, clock):
    rows = event_rows(track, context, clock)
    if not rows:
        return []
    occupied_rows = occupied(track, context, clock)
    # A phrase's enclosing span includes deliberate rests. Reward actual
    # written/sounding activity, without rewarding dense chord note counts.
    active = union([(b['start'], b['end']) for b in context['beats'] if not b['rest']] +
                   [(clock.quarter(n['t']), clock.quarter(n['t'] + n.get('sus', 0)))
                    for r in rows for n in r['notes']])
    end = timeline["measures"][-1]["quarter"] + timeline["measures"][-1]["quarters"]
    boundaries = {0.0: "song", end: "song"}
    for left, right in zip(occupied_rows, occupied_rows[1:]):
        if right[0] - left[1] >= 1 - EPS:
            boundaries[left[1]] = boundaries[right[0]] = "rest"
    measures = timeline["measures"]
    # A bounded exact repeated pattern establishes a real musical boundary.
    starts = [m["quarter"] for m in measures] + [end]
    for size in (1, 2, 4):
        signatures = [_fingerprint(rows, starts[i], starts[i + size], clock) for i in range(len(measures) - size + 1)]
        for i in range(len(measures) - 2 * size + 1):
            if signatures[i] is not None and signatures[i] == signatures[i + size]:
                for j in (i, i + size, i + 2 * size):
                    boundaries.setdefault(starts[j], "repeat")
    for q in context.get("sectionQuarters", []):
        boundaries[q] = "section"
    # Never break an occupied written slot, chord, strum, sustain or link.
    cuts = sorted(q for q in boundaries if not any(r["start"] + EPS < q < r["end"] - EPS for r in rows)
                  and not any(b["start"] + EPS < q < b["end"] - EPS for b in context["beats"] if not b["rest"]))
    out = []
    for a, b in zip(cuts, cuts[1:]):
        events = [r for r in rows if r["start"] >= a - EPS and r["end"] <= b + EPS and r["start"] < b - EPS]
        if not events:
            continue
        # Keep pickups and internal rests; only unused leading/trailing silence
        # is outside the passage. Its final written slot remains protected.
        lo, hi = min(r["start"] for r in events), max(r["end"] for r in events)
        out.append({"trackId": track["id"], "start": lo, "end": hi,
                    "activeQuarterBeats": round(sum(max(0, min(b, hi) - max(a, lo)) for a, b in active), 8),
                    "ghostOnly": all(n.get('ghost') for r in events for n in r['notes']),
                    "boundaries": [boundaries[a], boundaries[b]],
                    "boundaryQuarters": [a, b],
                    **_hand_endpoints(events),
                    "events": [{k: r[k] for k in ("kind", "index", "sourceIds", "occurrences")} for r in events]})
    return out


def omission_spans(performance, track_id, context, clock):
    """Reserve unsupported local source slots, not the entire donor track."""
    spans = []
    for row in performance.get('hybridSourceOmissions', []):
        if row.get('trackId') != track_id:
            continue
        lo = clock.quarter(row['scoreStart'])
        hi = clock.quarter(row['scoreStart'] + row.get('scoreDuration', 0))
        ids = set(row.get('sourceIds', row.get('source_ids', [])))
        eligible = [b for b in context['beats'] if not b['rest']
                    and (not ids or ids.intersection([b['sourceId'], *b['noteIds']]))]
        # High-fret receipts round source seconds to microseconds. Recover an
        # exact written onset before deciding which side of a bar it belongs to.
        near = [b for b in eligible if abs(b['start'] - lo) <= 1e-5]
        matching = near or [b for b in eligible if b['start'] - EPS <= lo < b['end'] - EPS]
        if near:
            lo = min(b['start'] for b in near)
        end_matches = [b['end'] for b in eligible if abs(b['end'] - hi) <= 1e-5]
        if end_matches:
            hi = min(end_matches, key=lambda q: abs(q - hi))
        spans.append([min([lo] + [b['start'] for b in matching]), max([hi] + [b['end'] for b in matching])])
    return union(spans)


def plan(performance, options, main_id, alignment, audio_duration, originals=None):
    from .hybrid_primary import backbone, finish
    primary = backbone(performance, options, main_id, alignment, audio_duration, originals)
    result = _fill_gaps(performance, options, main_id, alignment, audio_duration, primary, originals)
    return finish(result, primary, performance, options, alignment)


def _fill_gaps(performance, options, main_id, alignment, audio_duration, primary=None, originals=None):
    context = performance.get("compositionContext")
    if not context or context.get("version") != 1:
        raise ImportFailure("hybrid_failed", "Hybrid Lead needs the performed written score context.")
    clock = Clock(context["timeline"])
    end = clock.quarter(performance["duration"])
    by_id = {t["id"]: t for t in performance["tracks"]}
    main = by_id[main_id]
    protected = primary["protected"] if primary else occupied(main, context["tracks"][main_id], clock)
    fixed = _fixed_events(primary, main, context, clock)

    from functools import lru_cache
    @lru_cache(maxsize=50000)
    def guard(q, direction):
        # Evaluate both constraints under the actual recording map. Binary
        # search also handles nonlinear synchronization and tempo changes.
        origin = map_time(alignment, clock.seconds(q), allow_negative=True)
        available = max(0.0, end - q if direction > 0 else q)
        low, high = min(0.25, available), available
        for _ in range(45):
            middle = (low + high) / 2
            target = map_time(alignment, clock.seconds(min(end, max(0, q + direction * middle))), allow_negative=True)
            if abs(target - origin) >= 0.125:
                high = middle
            else:
                low = middle
        return max(0.25, high)

    gaps, left = [], 0.0
    for a, b in protected + [[end, end]]:
        lo = left + (guard(left, 1) if left > EPS else 0)
        hi = a - (guard(a, -1) if a < end - EPS else 0)
        if hi - lo >= 1 - EPS:
            gaps.append((lo, hi))
        left = max(left, b)
    excluded, candidates, evaluated, donor_sources = [], [], [], {}
    from .hybrid_variants import generate, MAX_VARIANTS, MAX_WINDOW_CHECKS
    variant_generation = {'candidateCount': 0, 'windowChecks': 0, 'budgetLimited': False,
                          'minActiveQuarterBeats': 4.0, 'minimumPitchedAttacks': 2}
    main_signature = _fingerprint(event_rows(main, context["tracks"][main_id], clock), 0, end, clock)
    signatures = {main_signature}
    preferred = options.get("preferredTrackIds", [])
    def decision(p, source):
        unsupported = source['unsupported']
        conflict = any(p['start'] < b - EPS and p['end'] > a + EPS for a, b in protected)
        fits = any(lo - EPS <= p['start'] and p['end'] <= hi + EPS for lo, hi in gaps)
        omitted = any(p['start'] < b - EPS and p['end'] > a + EPS or a == b and p['start'] - EPS <= a < p['end'] - EPS
                      for a, b in unsupported)
        return ('no_supported_passage' if p['ghostOnly'] else 'source_omissions' if omitted else
                'crosses_primary_material' if conflict else 'transition_guard_or_small_gap' if not fits else
                'past_recording_end' if map_time(alignment, clock.seconds(p['end']), allow_negative=True) > audio_duration + EPS else 'eligible_phrase')

    def record(p, reason):
        evaluated.append({k: deepcopy(p[k]) for k in ('trackId', 'start', 'end', 'boundaries', 'events', 'boundaryQuarters', 'variant', 'acceptedEnding') if k in p}
                         | {'reason': reason})

    donors = sorted(performance["tracks"], key=lambda t: (preferred.index(t["id"]) if t["id"] in preferred else len(preferred), t["id"]))
    for track in donors:
        ident = track["id"]
        if ident == main_id:
            continue
        reason = None
        if track["instrument"] != "guitar": reason = "not_guitar"
        elif ident in options.get("excludedTrackIds", []): reason = "excluded_by_user"
        elif track["tuning"] != main["tuning"] or track["capo"] != main["capo"]: reason = "incompatible_setup"
        elif re.search(r"\b(delay|echo|fx|effect)\b", track["name"], re.I): reason = "effect_layer"
        signature = None
        if reason is None:
            signature = _fingerprint(event_rows(track, context["tracks"][ident], clock), 0, end, clock)
            if signature in signatures:
                reason = "duplicate_main" if signature == main_signature else "duplicate_source"
        if reason:
            excluded.append({"trackId": ident, "reason": reason})
            continue
        signatures.add(signature)
        track_context = {**context["tracks"][ident], "sectionQuarters": [clock.quarter(s["time"]) for s in performance.get("sections", [])]}
        possible = passages(track, track_context, context["timeline"], clock)
        unsupported = omission_spans(performance, ident, track_context, clock)
        source = {'parents': possible, 'rows': event_rows(track, track_context, clock),
                  'context': track_context, 'unsupported': unsupported, 'track': track}
        donor_sources[ident] = source
        accepted, rejected_parents = [], []
        from .hybrid_endings import project
        for p in possible:
            p = project(p, source, (originals or {}).get(ident), clock, alignment, audio_duration)
            reason = decision(p, source)
            record(p, reason)
            if reason == 'eligible_phrase':
                accepted.append(p)
            elif reason in {'crosses_primary_material', 'transition_guard_or_small_gap'}:
                rejected_parents.append(p)
        variants, generation = generate(rejected_parents, source['rows'], track_context,
                                        context['timeline'], clock, gaps,
                                        max_variants=max(0, MAX_VARIANTS - variant_generation['candidateCount']),
                                        max_window_checks=max(0, MAX_WINDOW_CHECKS - variant_generation['windowChecks']),
                                        adjacent_parents=[*accepted, *rejected_parents])
        for field in ('candidateCount', 'windowChecks'):
            variant_generation[field] += generation[field]
        variant_generation['budgetLimited'] |= generation['budgetLimited']
        for p in variants:
            p = project(p, source, (originals or {}).get(ident), clock, alignment, audio_duration)
            reason = decision(p, source)
            record(p, reason)
            if reason == 'eligible_phrase':
                accepted.append(p)
        candidates.extend(accepted)
        if not accepted:
            excluded.append({"trackId": ident, "reason": "no_complete_passage_fits", "candidateCount": len(possible)})
    # All candidates are whole, primary-safe passages. A bounded DAG keeps a
    # feasible incumbent instead of requiring the user to resolve search limits.
    from .hybrid_search import select_passages
    candidates.sort(key=lambda p: (p["end"], p["start"], p["trackId"]))
    chosen_indices, best, utility, boundary_cost = [], 0.0, 0.0, 0.0
    gap_selections, gap_plans = [], []
    states = operations = 0
    reasons = []
    if variant_generation['budgetLimited']:
        reasons.append('variant_generation_limit')
    for lo, hi in gaps:
        indices = [i for i, p in enumerate(candidates) if lo - EPS <= p["start"] and p["end"] <= hi + EPS]
        left_anchor = max((p for p in fixed if p['end'] <= lo + EPS),
                          key=lambda p: (p['end'], p['start'], p['trackId']), default=None)
        right_anchor = min((p for p in fixed if p['start'] >= hi - EPS),
                           key=lambda p: (p['start'], p['end'], p['trackId']), default=None)
        search = select_passages([candidates[i] for i in indices], guard, preferred,
                                 max_candidates=MAX_CANDIDATES, max_states=max(0, MAX_STATES - states),
                                 max_operations=max(0, MAX_OPERATIONS - operations),
                                 left_anchor=left_anchor, right_anchor=right_anchor)
        gap_plans.append({'gap': (lo, hi), 'indices': indices, 'search': search,
                          'left': left_anchor, 'right': right_anchor})
        states += search['states']
        operations += search['operations']
        if search['reason']:
            reasons.extend(search['reason'].split(','))
    # Finish every original gap first. Refinement can consume only the remaining
    # shared budgets and always has the complete original song plan to retain.
    from .hybrid_optional import refine_foreground, refine_neighbors
    from .hybrid_optional_foreground import (MAX_CANDIDATES as FOREGROUND_CANDIDATES,
                                            MAX_OPERATIONS as FOREGROUND_OPERATIONS,
                                            MAX_EPISODES as FOREGROUND_EPISODES)
    foreground_report = {'algorithm': 'local-optional-foreground-v1', 'candidateCount': 0,
                         'operations': 0, 'episodes': [], 'acceptedGaps': 0,
                         'rejectedGaps': 0, 'budgetLimited': False, 'activityChangeQuarterBeats': 0,
                         'decisions': [], 'allowedEdgeOmissions': []}
    neighbor_report = {'candidateCount': 0, 'windowChecks': 0, 'acceptedGaps': 0,
                       'rejectedGaps': 0, 'budgetLimited': False}
    for item in gap_plans:
        lo, hi = item['gap']
        indices, search = item['indices'], item['search']
        nodes = [candidates[i] for i in indices]
        expanded, search, foreground = refine_foreground(
            nodes, search, donor_sources, by_id, context['timeline'], clock, (lo, hi), guard, preferred,
            options=options, main_id=main_id, validate=decision,
            max_variants=max(0, MAX_VARIANTS - variant_generation['candidateCount']),
            max_window_checks=max(0, MAX_WINDOW_CHECKS - variant_generation['windowChecks']),
            max_candidates=max(0, MAX_CANDIDATES - len(candidates) + len(nodes)),
            max_states=max(0, MAX_STATES - states), max_operations=max(0, MAX_OPERATIONS - operations),
            max_recognition_candidates=max(0, FOREGROUND_CANDIDATES - foreground_report['candidateCount']),
            max_recognition_operations=max(0, FOREGROUND_OPERATIONS - foreground_report['operations']),
            max_episodes=max(0, FOREGROUND_EPISODES - len(foreground_report['episodes'])),
            left_anchor=item['left'], right_anchor=item['right'])
        for p in expanded[len(nodes):]:
            indices.append(len(candidates))
            candidates.append(p)
            record(p, 'eligible_phrase')
        nodes = expanded
        for field in ('candidateCount', 'windowChecks'):
            variant_generation[field] += foreground[field]
        variant_generation['budgetLimited'] |= foreground['budgetLimited']
        states += foreground['states']
        operations += foreground['operations']
        foreground_report['candidateCount'] += foreground['recognition']['candidateCount']
        foreground_report['operations'] += foreground['recognition']['operations']
        foreground_report['episodes'].extend(foreground['episodes'])
        foreground_report['allowedEdgeOmissions'].extend(foreground.get('allowedEdgeOmissions', []))
        foreground_report['acceptedGaps'] += int(foreground['accepted'])
        foreground_report['rejectedGaps'] += int('rejectedReason' in foreground)
        foreground_report['budgetLimited'] |= foreground['budgetLimited']
        if foreground['accepted']:
            foreground_report['activityChangeQuarterBeats'] += foreground.get('activityChangeQuarterBeats', 0)
        if foreground['recognition']['episodes']:
            foreground_report['decisions'].append({'gap': [lo, hi], 'accepted': foreground['accepted'],
                'episodeIds': [e['candidateId'] for e in foreground['recognition']['episodes']],
                **{k: deepcopy(foreground[k]) for k in ('rejectedReason', 'reason', 'activityChangeQuarterBeats',
                                                       'allowedActivityLossQuarterBeats', 'missingOutsideCount',
                                                       'missingOutsideReferences', 'beforeActiveQuarterBeats',
                                                       'afterActiveQuarterBeats') if k in foreground}})
        if foreground['reason']:
            reasons.extend(foreground['reason'].split(','))
        expanded, refined, info = refine_neighbors(
            nodes, search, donor_sources, context['timeline'], clock, (lo, hi), guard, preferred,
            validate=decision, max_variants=max(0, MAX_VARIANTS - variant_generation['candidateCount']),
            max_window_checks=max(0, MAX_WINDOW_CHECKS - variant_generation['windowChecks']),
            max_candidates=max(0, MAX_CANDIDATES - len(candidates) + len(nodes)),
            max_states=max(0, MAX_STATES - states), max_operations=max(0, MAX_OPERATIONS - operations),
            left_anchor=item['left'], right_anchor=item['right'],
            edge_omissions=foreground.get('allowedEdgeOmissions', []))
        for p in expanded[len(nodes):]:
            indices.append(len(candidates))
            candidates.append(p)
            record(p, 'eligible_phrase')
        search = refined
        for field in ('candidateCount', 'windowChecks'):
            variant_generation[field] += info[field]
            neighbor_report[field] += info[field]
        variant_generation['budgetLimited'] |= info['budgetLimited']
        neighbor_report['budgetLimited'] |= info['budgetLimited']
        neighbor_report['acceptedGaps'] += int(info['accepted'])
        neighbor_report['rejectedGaps'] += int('rejectedReason' in info)
        if info.get('restoredEdgeOmissionIndices'):
            restored = [foreground['allowedEdgeOmissions'][i] for i in info['restoredEdgeOmissionIndices']]
            foreground_report['allowedEdgeOmissions'] = [r for r in foreground_report['allowedEdgeOmissions'] if r not in restored]
        states += info['states']
        operations += info['operations']
        if info['reason']:
            reasons.extend(info['reason'].split(','))
        chosen_indices.extend(indices[i] for i in search['indices'])
        best += search['activeQuarterBeats']
        utility += search['utilityQuarterBeats']
        boundary_cost += search['boundaryCostQuarterBeats']
        if indices:
            gap_selections.append({'start': lo, 'end': hi, 'leftAnchor': item['left'], 'rightAnchor': item['right'],
                                   **{k: search[k] for k in ('activeQuarterBeats', 'utilityQuarterBeats', 'switches',
                                                            'anchorTransitionBaselineQuarterBeats', 'boundaryCostQuarterBeats', 'transitions')},
                                   'selected': [{'trackId': candidates[indices[i]]['trackId'],
                                                 'start': candidates[indices[i]]['start'], 'end': candidates[indices[i]]['end']}
                                                for i in search['indices']]})
    selected = [deepcopy(candidates[i]) for i in chosen_indices]
    for p in selected:
        p["recordingStart"] = round(map_time(alignment, clock.seconds(p["start"]), allow_negative=True), 6)
        p["recordingEnd"] = round(map_time(alignment, clock.seconds(p["end"]), allow_negative=True), 6)
    contributing = {p["trackId"] for p in selected}
    chosen_set = set(chosen_indices)
    for tid in sorted({p["trackId"] for p in candidates} - contributing):
        excluded.append({"trackId": tid, "reason": "another_passage_plan_selected"})
    return {"version": 1, "policy": POLICY, "mainTrackId": main_id, "passages": selected,
            "excluded": excluded, "candidateCount": len(candidates), "status": "created" if selected else "no_additions",
            "protected": protected, "gaps": gaps, "addedQuarterBeats": sum(p["end"] - p["start"] for p in selected),
            "candidateDecisions": evaluated,
            "selection": {"algorithm": "bounded-whole-phrase-dag-v3", "bestQuarterBeats": round(best, 6),
                          'variantGeneration': variant_generation,
                          'neighborRefinement': neighbor_report,
                          'optionalForeground': foreground_report,
                          "activeQuarterBeats": round(best, 6), "candidateCount": len(candidates),
                          "utilityQuarterBeats": round(utility, 6), "gaps": gap_selections,
                          'boundaryCostQuarterBeats': round(boundary_cost, 6),
                          "states": states, "operations": operations, "budgetLimited": bool(reasons),
                          "reason": ','.join(dict.fromkeys(reasons)) or None,
                          "priority": "explicit-source-activity, activity-minus-boundary-entry-return-switch-and-movement-cost, continuity, stable-id"},
            "unselected": [{k: p[k] for k in ("trackId", "start", "end", "boundaries")} for i, p in enumerate(candidates) if i not in chosen_set]}
