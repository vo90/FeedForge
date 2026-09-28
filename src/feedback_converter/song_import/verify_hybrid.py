"""Independent composition audit, using independently read source facts.

Does not import the composer, its context capture, or its materializer. Original
charts are checked by verification.py first; this checker validates an exact
copy/membership proof, original setup, written occupancy and whole boundaries.
It does not reproduce the producer's musical scoring. Regional policies also
check conservative independently established solo-owner obligations.
"""
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
import math
import re

from .verify_timeline import Clock, RecordingMap
from fractions import Fraction
from functools import lru_cache

POLICY = "hybrid-lead-v1"
TOL = 0.0000011


def partition(manifest, check, requested=None):
    recipe = manifest.get("song_import", {})
    options = recipe.get("hybridLead", {"enabled": False})
    if requested is not None:
        check.equal("hybrid_request", "song_import/hybridLead", requested, options)
    all_items = manifest.get("arrangements", [])
    derived = [a for a in all_items if a.get("derived")]
    if options.get("enabled") is not True:
        check.equal("hybrid_unrequested", "arrangements", [], derived)
        check.equal("hybrid_unrequested", "song_import/hybridLeadFile", None, recipe.get("hybridLeadFile"))
        return all_items, []
    if recipe.get("preservationContract", 0) < 32:
        check.fail("hybrid_contract", "song_import", "Hybrid Lead requires preservation contract 32.")
    policy = options.get('policy', POLICY)
    if policy not in {POLICY, 'hybrid-lead-v2', 'hybrid-lead-v3'}:
        check.fail('hybrid_policy', 'song_import/hybridLead', 'Unsupported musical policy.')
    if policy == 'hybrid-lead-v2' and recipe.get('preservationContract', 0) < 33:
        check.fail('hybrid_contract', 'song_import', 'Lead/solo-first composition requires preservation contract 33.')
    if policy == 'hybrid-lead-v3' and recipe.get('preservationContract', 0) < 34:
        check.fail('hybrid_contract', 'song_import', 'Regional lead composition requires preservation contract 34.')
    for a in derived:
        check.equal("hybrid_identity", "arrangements/derived", {"kind": policy, "receipt": "import/hybrid-lead.json"}, a["derived"])
    return [a for a in all_items if not a.get("derived")], derived


def _hash(z, name):
    return hashlib.sha256(z.read(name)).hexdigest()


def _signature(part, bar):
    # Source-native written patterns, independent of sounding duration and the
    # producer's optional notation. All technique fields participate.
    atoms = []
    for n in part.bars[bar]:
        atom = asdict(n)
        for key in ("location", "voice", "beat"):
            atom.pop(key, None)
        atoms.append(atom)
    # A chord's source-note array can list its strings in either order. Musical
    # equality depends on the timed atoms (including duplicates), not that order.
    atoms.sort(key=lambda atom: json.dumps(atom, sort_keys=True, default=str))
    beats = [{k: b[k] for k in ("q", "length", "rest")} for b in part.beats[bar]]
    return json.dumps([atoms, beats], sort_keys=True, default=str)


def verify(z, manifest, originals, derived, facts, source, alignment, source_hash, check):
    recipe = manifest.get("song_import", {})
    options = recipe.get("hybridLead", {"enabled": False})
    regional_policy = options.get('policy') == 'hybrid-lead-v3'
    primary_policy = regional_policy or options.get('policy') == 'hybrid-lead-v2'
    policy = options.get('policy') if primary_policy else POLICY
    if options.get("enabled") is not True:
        return None
    playable_guitars = [a for a in originals if a.get("type") in {"guitar", "lead", "rhythm"}]
    check.equal("hybrid_count", "arrangements", 1 if playable_guitars else 0, len(derived))
    if not playable_guitars:
        check.equal("hybrid_not_applicable", "song_import/hybridLeadResult/status", "not_applicable", recipe.get("hybridLeadResult", {}).get("status"))
        check.equal("hybrid_not_applicable", "song_import/hybridLeadFile", None, recipe.get("hybridLeadFile"))
        return {"status": "not_applicable"}
    if len(derived) != 1:
        return None
    check.equal("hybrid_receipt", "song_import/hybridLeadFile", "import/hybrid-lead.json", recipe.get("hybridLeadFile"))
    from .verification import _json
    receipt = _json(z, "import/hybrid-lead.json", check)
    if regional_policy and recipe.get('preservationContract', 0) >= 35:
        revision = 4 if recipe.get('preservationContract', 0) >= 37 else 3 if recipe.get('preservationContract', 0) >= 36 else 2
        check.equal('hybrid_selection_revision', 'hybrid/selectionRevision', revision, receipt.get('selectionRevision'))
    for key, expected in (("version", 3 if regional_policy else 2 if primary_policy else 1), ("policy", policy), ("sourceSha256", source_hash), ("options", options), ("audioSha256", recipe.get("audioHash"))):
        check.equal("hybrid_receipt", "hybrid/" + key, expected, receipt.get(key))
    if "archiveSha256" in receipt or "outputHash" in receipt:
        check.fail("hybrid_circular_hash", "hybrid", "Final archive hashes belong in external evidence.")
    main_id = options.get("mainTrackId")
    check.equal("hybrid_main", "hybrid/mainTrackId", main_id, receipt.get("mainTrackId"))
    check.equal("hybrid_source_binding", "hybrid/options/sourceSha256", source_hash, options.get("sourceSha256"))
    ident = "hybrid-lead-" + hashlib.sha256(((policy + ':') if primary_policy else '').encode() + (source_hash + ":" + str(main_id)).encode()).hexdigest()[:20]
    arr = derived[0]
    for key, expected in (("id", ident), ("name", "Hybrid Lead"), ("type", "lead"), ("file", f"arrangements/{ident}.json")):
        check.equal("hybrid_identity", "arrangements/" + key, expected, arr.get(key))
    check.equal("hybrid_identity", "hybrid/arrangementId", ident, receipt.get("arrangementId"))
    chart = _json(z, arr["file"], check)
    check.equal("hybrid_hash", "hybrid/chartSha256", _hash(z, arr["file"]), receipt.get("chartSha256"))
    source_rows, charts, parts = {}, {}, {p["source"].id: p for p in facts["parts"]}
    for row in receipt.get("sources", []):
        track_id = row["trackId"]
        if track_id in source_rows:
            check.fail("hybrid_source", "hybrid/sources", "Duplicate source identity.")
        source_rows[track_id] = row
        matches = [a for a in originals if a["id"] == row["arrangementId"]]
        if len(matches) != 1 or track_id not in parts:
            check.fail("hybrid_source", "hybrid/sources", "Unknown original guitar.")
            continue
        original = matches[0]
        check.equal("hybrid_source", "hybrid/sources/file", original["file"], row["file"])
        check.equal("hybrid_source_hash", "hybrid/sources/chartSha256", _hash(z, row["file"]), row.get("chartSha256"))
        if original.get("notation"):
            check.equal("hybrid_source_notation", "hybrid/sources/notationFile", original["notation"], row.get("notationFile"))
            check.equal("hybrid_source_notation", "hybrid/sources/notationSha256", _hash(z, original["notation"]), row.get("notationSha256"))
        part = parts[track_id]["source"]
        original_id, voices = track_id, None
        for projection in facts.get("voice_projection", {}).get("tracks", []):
            for a in projection["arrangements"]:
                if a["id"] == track_id:
                    original_id, voices = projection["sourceTrackId"], a["voices"]
        check.equal("hybrid_source_voice", "hybrid/sources/sourceTrackId", original_id, row.get("sourceTrackId"))
        check.equal("hybrid_source_voice", "hybrid/sources/voices", voices, row.get("voices"))
        if part.instrument != "guitar":
            check.fail("hybrid_source", "hybrid/sources", "Only guitars may contribute.")
        charts[track_id] = _json(z, row["file"], check)
    if main_id not in charts or main_id not in parts:
        check.fail("hybrid_main", "hybrid", "The main guitar is missing.")
        return None
    main = charts[main_id]
    check.equal("hybrid_setup", "arrangement/tuning", main["tuning"], arr.get("tuning"))
    check.equal("hybrid_setup", "arrangement/capo", main["capo"], arr.get("capo", 0))
    expected_chart = deepcopy(main)
    expected_chart["name"] = "Hybrid Lead"
    expected_chart.pop("phrases", None)
    expected_chart.pop("ext", None)
    offsets = {main_id: 0}
    order = facts["order"]
    clock = Clock(source, order)
    recording = RecordingMap(alignment)
    at = lambda q: float(recording.at(clock.at(Fraction(str(q)))))
    @lru_cache(maxsize=100000)
    def quarter_at(seconds):
        low, high = 0.0, float(clock.quarters)
        for _ in range(45):
            mid = (low + high) / 2
            if at(mid) < seconds:
                low = mid
            else:
                high = mid
        return (low + high) / 2
    # Derive occupancy from independent written facts, including source notes
    # that the original chart legitimately omitted for high frets/endings.
    protected = [(b["time"], b["end"]) for b in parts[main_id]["notation_beats"] if not b["rest"]]
    protected += [(n["note"]["t"], n["note"]["t"] + n["note"].get("sus", 0)) for n in parts[main_id]["notes"]]
    prior_by_string = {}
    for row in parts[main_id]["notes"]:
        note = row["note"]
        prior = prior_by_string.get(note["s"])
        if prior and (note.get("ho") or note.get("po") or prior.get("ln") or prior.get("sl") is not None or prior.get("slu")):
            protected.append((prior["t"], note["t"] + note.get("sus", 0)))
        prior_by_string[note["s"]] = note
    occupied_q = []
    main_part = parts[main_id]["source"]
    for occurrence, bi in enumerate(order):
        origin = clock.measure_starts[occurrence]
        occupied_q.extend((float(origin + b["q"]), float(origin + b["q"] + b["length"])) for b in main_part.beats[bi] if not b["rest"])
    occupied_q.extend((quarter_at(a), quarter_at(b)) for a, b in protected)
    musical_audit = {}
    if primary_policy:
        from .verify_hybrid_priority import audit
        all_charts = {a['id']: _json(z, a['file'], check) for a in originals if a.get('type') in {'guitar','lead','rhythm'}}
        kept, removed, event_facts, primary_protected = audit(all_charts, receipt, facts, options, quarter_at, at, manifest['duration'], source.format, check, source=source, musical_audit=musical_audit)
        for kind in ('notes','chords'):
            expected_chart[kind] = [e for i,e in enumerate(main[kind]) if (kind,i) in kept]
        occupied_q = [(event_facts[main_id][k]['start'],event_facts[main_id][k]['end']) for k in kept]
        protected = [(at(a),at(b)) for a,b in occupied_q]
    used_refs, seen_sources, previous = set(), {main_id}, None
    for passage in receipt.get("passages", []):
        tid, lo, hi = passage["trackId"], passage["start"], passage["end"]
        if tid == main_id or tid not in charts or tid in options.get("excludedTrackIds", []):
            check.fail("hybrid_donor", "hybrid/passages", "An excluded or unknown source contributes.")
            continue
        seen_sources.add(tid)
        donor = charts[tid]
        for key in ("tuning", "capo"):
            check.equal("hybrid_setup", "hybrid/passages/" + key, main.get(key), donor.get(key))
        a, b = at(lo), at(hi)
        primary_passage = primary_policy and passage.get('priority') in {'solo','lead'}
        check.near("hybrid_time", "hybrid/recordingStart", a, passage.get("recordingStart"))
        check.near("hybrid_time", "hybrid/recordingEnd", b, passage.get("recordingEnd"))
        if hi <= lo or a < -TOL or b > manifest["duration"] + TOL:
            check.fail("hybrid_bounds", "hybrid/passages", "Passage is outside the recording.")
        margin = 0 if primary_passage else .125
        qmargin = 0 if primary_passage else .25
        protected_here = protected if not primary_policy or primary_passage else [(at(x),at(y)) for x,y in primary_protected]
        occupied_here = occupied_q if not primary_policy or primary_passage else primary_protected
        if any(a < end + margin - TOL and b > start - margin + TOL for start, end in protected_here):
            check.fail("hybrid_main_overlap", "hybrid/passages", "Passage crosses the main guitar or its recording-time transition guard.")
        if any(lo < end + qmargin - 1e-5 and hi > start - qmargin + 1e-5 for start, end in occupied_here):
            check.fail("hybrid_main_overlap", "hybrid/passages", "Passage crosses the main guitar's written slot or beat guard.")
        left = max((end for start, end in occupied_here if end <= lo + 1e-7), default=0)
        right = min((start for start, end in occupied_here if start >= hi - 1e-7), default=float(clock.quarters))
        gap_lo = max(left + .25, quarter_at(at(left) + .125)) if left > 1e-7 else 0
        gap_hi = min(right - .25, quarter_at(at(right) - .125)) if right < float(clock.quarters) - 1e-7 else right
        if not primary_passage and gap_hi - gap_lo < 1 - 1e-6:
            check.fail("hybrid_gap", "hybrid/passages", "The guarded main gap is shorter than one beat.")
        if previous:
            exact = previous["trackId"] == tid or primary_passage or primary_policy and previous.get('priority') in {'solo','lead'}
            margin = 0 if exact else .125
            qmargin = 0 if exact else .25
            if a < previous["recordingEnd"] + margin - TOL or lo < previous["end"] + qmargin - 1e-7:
                check.fail("hybrid_donor_overlap", "hybrid/passages", "Supplementary passages overlap or switch without a guard.")
        previous = passage
        if tid not in offsets:
            offsets[tid] = len(expected_chart["templates"])
            expected_chart["templates"].extend(deepcopy(donor["templates"]))
        # All events inside a passage must be copied, with no cropped tails or
        # unlisted injected events. Whole written slots must also fit.
        refs = {(r["kind"], r["index"]) for r in passage["events"]}
        wanted_refs = set()
        owned_a = at(passage.get('ownedStart', lo)) if regional_policy and primary_passage else a
        owned_b = at(passage.get('ownedEnd', hi)) if regional_policy and primary_passage else b
        if regional_policy and primary_passage and (owned_b <= owned_a or owned_a < -TOL or owned_b > manifest['duration']+TOL):
            check.fail('hybrid_primary_boundary', 'hybrid/passages/ownedStart', 'Primary attack ownership has invalid recording bounds.')
        if regional_policy and primary_passage:
            check.near('hybrid_time', 'hybrid/passages/recordingOwnedStart', owned_a, passage.get('recordingOwnedStart'))
            check.near('hybrid_time', 'hybrid/passages/recordingOwnedEnd', owned_b, passage.get('recordingOwnedEnd'))
        for kind in ("notes", "chords"):
            for index, event in enumerate(donor[kind]):
                notes = [event] if kind == "notes" else [{"t": event["t"], **n} for n in event["notes"]]
                start = min(n["t"] for n in notes)
                end = max(n["t"] + n.get("sus", 0) for n in notes)
                if owned_a - TOL <= start < owned_b - TOL:
                    wanted_refs.add((kind, index))
                    if end > b + TOL:
                        check.fail("hybrid_cropped_tail", "hybrid/passages", "A selected note continues beyond the passage.")
        if regional_policy and primary_passage:
            spans = {(event_facts[tid][key]['start'], event_facts[tid][key]['end']) for key in wanted_refs}
            wanted_refs.update(key for key, row in event_facts[tid].items() if (row['start'], row['end']) in spans)
            for key in wanted_refs:
                row = event_facts[tid][key]
                if row['start'] < lo-1e-5 or row['end'] > hi+1e-5:
                    check.fail('hybrid_cropped_tail', 'hybrid/passages', 'A hard source dependency is outside the complete primary footprint.')
        check.equal("hybrid_passage_membership", "hybrid/passages/events", sorted(wanted_refs), sorted(refs))
        if len(refs) != len(passage["events"]) or not refs:
            check.fail("hybrid_passage_membership", "hybrid/passages", "Duplicate or empty passage.")
        for beat in parts[tid]["notation_beats"]:
            if regional_policy and primary_passage:
                break  # Selected event footprints above close written dependencies.
            if beat["rest"]:
                continue
            if beat["time"] < b - TOL and beat["end"] > a + TOL and (beat["time"] < a - TOL or beat["end"] > b + TOL):
                check.fail("hybrid_written_cut", "hybrid/passages", "A written slot was split.")
        for ref in passage["events"]:
            key = (tid, ref["kind"], ref["index"])
            if key in used_refs:
                check.fail("hybrid_duplicate", "hybrid/passages", "A donor event was inserted twice.")
            used_refs.add(key)
            event = deepcopy(donor[ref["kind"]][ref["index"]])
            copied = [event] if ref["kind"] == "notes" else [{"t": event["t"], **n} for n in event["notes"]]
            matches = [n for n in parts[tid]["notes"] if any(abs(n["note"]["t"] - c["t"]) <= TOL and
                n["note"]["s"] == c["s"] and n["note"]["f"] == c["f"] for c in copied)]
            beat_ids = {beat['location']: beat.get('source_id') for bar in parts[tid]['source'].beats for beat in bar}
            def source_id(location):
                if source.format == 'songsterr':
                    return "songsterr:" + ":".join(re.findall(r"\d+", location))
                if location in beat_ids:
                    return beat_ids[location]
                parent, _, nid = location.rsplit('/', 2)
                return beat_ids[parent] + ':' + nid
            expected_ids = {source_id(loc) for n in matches for loc in n["locations"]}
            if ref["kind"] == "chords":
                expected_ids.update(source_id(n["beat"]) for n in matches)
            check.equal("hybrid_event_lineage", "hybrid/passages/sourceIds", sorted(expected_ids), ref.get("sourceIds"))
            if any(type(o) is not int or o < 1 or o > len(order) for o in ref.get("occurrences", [])) or not ref.get("occurrences"):
                check.fail("hybrid_event_lineage", "hybrid/passages/occurrences", "Invalid performed occurrence.")
            locations = {loc.rsplit('/', 2)[0] for n in matches for loc in n["locations"]}
            earliest = min((c['t'] for c in copied), default=a)
            latest = max((c['t'] + c.get('sus', 0) for c in copied), default=b)
            occurrences = {beat['measure'] for beat in parts[tid]['notation_beats'] if beat['location'] in locations
                           and beat['time'] <= latest + TOL and beat['end'] >= earliest - TOL
                           and a - TOL <= beat['time'] < b - TOL}
            if primary_policy:
                occurrences = event_facts[tid][(ref['kind'],ref['index'])]['occurrences']
            check.equal("hybrid_event_lineage", "hybrid/passages/occurrences", sorted(occurrences), ref.get("occurrences"))
            if ref["kind"] == "chords":
                event["id"] += offsets[tid]
            expected_chart[ref["kind"]].append(event)
        if primary_passage:
            continue  # Primary boundaries/coverage are independently checked by audit().
        # Verify the declared source-supported boundary independently.
        part = parts[tid]["source"]
        intervals = []
        for occurrence, bi in enumerate(order):
            origin = clock.measure_starts[occurrence]
            intervals.extend((float(origin + beat["q"]), float(origin + beat["q"] + beat["length"])) for beat in part.beats[bi] if not beat["rest"])
        starts = [float(x) for x in clock.measure_starts] + [float(clock.quarters)]
        signatures = [_signature(part, bi) for bi in order]
        sections = {starts[i] for i, bi in enumerate(order) if source.bars[bi].section}
        bounds = passage.get("boundaryQuarters", [])
        variant = passage.get('variant')
        safe_variant = regional_policy and recipe.get('preservationContract', 0) >= 36 and isinstance(variant, dict)
        if variant is not None:
            from .verify_hybrid_opportunities import active_quarters, pitched
            parent_bounds = variant.get('parentBoundaryQuarters', []) if isinstance(variant, dict) else []
            parent_labels = variant.get('parentBoundaries', []) if isinstance(variant, dict) else []
            retained_boundary = (isinstance(variant, dict) and variant.get('kind') == 'retained_optional_boundary'
                                 and recipe.get('preservationContract', 0) >= 37)
            safe_variant = (safe_variant and (variant.get('kind') == 'gap_safe_subphrase' or retained_boundary)
                            and len(parent_bounds) == 2 and len(parent_labels) == 2
                            and all(type(value) in (int, float) and math.isfinite(value)
                                    for value in [*parent_bounds, variant.get('parentStart'), variant.get('parentEnd')])
                            and parent_bounds[0] <= variant['parentStart'] + 1e-5
                            and variant['parentStart'] <= lo + 1e-5
                            and hi <= variant['parentEnd'] + 1e-5
                            and variant['parentEnd'] <= parent_bounds[1] + 1e-5
                            and all(label in ({'song', 'section', 'rest', 'repeat', 'bar', 'gesture'} if retained_boundary
                                              else {'song', 'section', 'rest', 'repeat'}) for label in parent_labels))
            if not safe_variant:
                check.fail('hybrid_subphrase', 'hybrid/passages/variant', 'Invalid source subphrase provenance.')
            if regional_policy:
                members = {key: event_facts[tid][key] for key in refs if key in event_facts[tid]}
                attacks = {round(row['onset'], 5) for row in members.values() if any(pitched(n) for n in row['notes'])}
                if len(attacks) < 2 or (not retained_boundary and active_quarters(members, parts[tid], lo, hi, quarter_at) < 4 - 1e-5):
                    check.fail('hybrid_subphrase', 'hybrid/passages/variant', 'An optional subphrase needs four active beats and two pitched attacks.')
            if retained_boundary:
                from .verify_hybrid_optional import audit_retained_boundary, audit_retained_parent_source
                audit_retained_boundary(passage, receipt, event_facts, at, check)
                audit_retained_parent_source(passage, source, part, order, clock, event_facts.get(tid, {}), check)
        if len(bounds) == 2:
            boundary_left, boundary_right = bounds
            complete = [(start, end) for start, end in intervals if boundary_left - 1e-7 <= start < boundary_right - 1e-7]
            complete.extend((quarter_at(row['note']['t']), quarter_at(row['note']['t'] + row['note'].get('sus', 0)))
                            for row in parts[tid]['notes'] if at(boundary_left) - TOL <= row['note']['t'] < at(boundary_right) - TOL)
            if not complete or boundary_left > lo + 1e-7 or boundary_right < hi - 1e-7:
                check.fail("hybrid_whole_passage", "hybrid/passages", "Passage has invalid enclosing boundaries.")
            else:
                check.near("hybrid_whole_passage", "hybrid/passages/start", min(x[0] for x in complete), lo, 1e-5)
                check.near("hybrid_whole_passage", "hybrid/passages/end", max(x[1] for x in complete), hi, 1e-5)
        for q, label in zip(passage.get("boundaryQuarters", []), passage.get("boundaries", [])):
            valid = label == "song" and any(abs(q-x) < 1e-7 for x in (0,float(clock.quarters))) or label == "section" and any(abs(q-x) < 1e-7 for x in sections)
            if label == "rest":
                left = max((end for start, end in intervals if end <= q + 1e-7), default=0)
                right = min((start for start, end in intervals if start >= q - 1e-7), default=float(clock.quarters))
                valid = right - left >= 1 - 1e-7 and not any(start + 1e-7 < q < end - 1e-7 for start, end in intervals)
            boundary_index = next((i for i,x in enumerate(starts) if abs(q-x) < 1e-7),None)
            if label == "repeat" and boundary_index is not None:
                j = boundary_index
                valid = any(0 <= i and i + 2 * size <= len(order) and signatures[i:i + size] == signatures[i + size:i + 2 * size]
                            for size in (1, 2, 4) for i in (j, j - size, j - 2 * size))
            if label in {'bar', 'gesture'} and safe_variant:
                values = event_facts[tid].values()
                edge = (boundary_index is not None if label == 'bar' else
                        any(abs(q - value) <= 1e-5 for row in values for value in (row['start'], row['end'])))
                valid = (edge and not any(row['start'] + 1e-5 < q < row['end'] - 1e-5 for row in values)
                         and not any(start + 1e-5 < q < end - 1e-5 for start, end in intervals))
            if not valid:
                check.fail("hybrid_boundary", f"hybrid/passages/{tid}/{q}/{label}", "A claimed musical boundary is unsupported by the source.")
        if len(passage.get("boundaryQuarters", [])) != 2 or len(passage.get("boundaries", [])) != 2:
            check.fail("hybrid_boundary", "hybrid/passages", "Two source-supported boundaries are required.")
        if (recipe.get('preservationContract', 0) >= 37 and isinstance(variant, dict)
                and variant.get('kind') == 'gap_safe_subphrase'):
            from .verify_hybrid_continuity import audit_section_join
            audit_section_join(passage, source, part, order, clock, event_facts.get(tid, {}), check)
    if regional_policy and recipe.get('preservationContract', 0) >= 37:
        from .verify_hybrid_optional import audit_edge_omissions
        audit_edge_omissions(receipt, event_facts, parts, source, order, clock, at, quarter_at,
                            primary_protected, main_id, check)
    check.equal("hybrid_sources", "hybrid/sources", sorted(seen_sources), sorted(source_rows))
    for key in ("notes", "chords"):
        expected_chart[key].sort(key=lambda e: e["t"])
    if primary_policy:
        referenced = {c['id'] for c in expected_chart['chords']}
        indices = [i for i,t in enumerate(expected_chart['templates']) if i in referenced or max(t['frets'], default=-1) <= 24]
        ids = {old:new for new,old in enumerate(indices)}
        expected_chart['templates'] = [expected_chart['templates'][i] for i in indices]
        for chord in expected_chart['chords']:
            chord['id'] = ids[chord['id']]
    actual_chart = {k: v for k, v in chart.items() if k not in {"phrases", "ext"}}
    check.equal("hybrid_chart", arr["file"], expected_chart, actual_chart)
    if regional_policy and recipe.get('preservationContract', 0) >= 35:
        # Interval arithmetic is descriptive, not a shared musical oracle.
        # Rebuild its inputs from independently verified originals and the
        # actual hybrid chart; receipt text cannot excuse absent activity.
        from .hybrid_reporting import activity_summary
        tracks = [{'id': tid, 'name': part['source'].name, 'instrument': part['source'].instrument,
                   'tuning': list(part['source'].tuning), 'capo': part['source'].capo}
                  for tid, part in parts.items()]
        activity = activity_summary({tid: {'chart': original} for tid, original in all_charts.items()},
                                    tracks, main_id, chart, include_rest_windows=recipe.get('preservationContract', 0) >= 36)
        check.equal('hybrid_tab_activity', 'hybrid/tabActivity', activity, receipt.get('tabActivity'))
        summary = recipe.get('hybridLeadResult', {})
        check.equal('hybrid_tab_activity', 'song_import/hybridLeadResult/tabActivity', activity, summary.get('tabActivity'))
        check.equal('hybrid_selection_revision', 'song_import/hybridLeadResult/selectionRevision',
                    4 if recipe.get('preservationContract', 0) >= 37 else 3 if recipe.get('preservationContract', 0) >= 36 else 2, summary.get('selectionRevision'))
        check.equal('hybrid_coverage_scope', 'song_import/hybridLeadResult/coverageScope',
                    'identified_lead_requirements', summary.get('coverageScope'))
    check.equal("hybrid_status", "hybrid/status", "created" if receipt.get("passages") else "no_additions", receipt.get("status"))
    check.equal("hybrid_count", "arrangements/event_count", len(chart["notes"]) + len(chart["chords"]), arr.get("event_count"))
    check.equal("hybrid_count", "arrangements/note_count", len(chart["notes"]) + sum(len(c["notes"]) for c in chart["chords"]), arr.get("note_count"))
    if arr.get("notation"):
        check.equal("hybrid_notation_hash", "hybrid/notationSha256", _hash(z, arr["notation"]), receipt.get("notationSha256"))
        _notation(z, arr, receipt, source_rows, check)
    elif receipt.get("notationStatus") != "source_only":
        check.fail("hybrid_notation", "hybrid", "Notation was lost without a declared source limitation.")
    return {"status": receipt["status"], "passageCount": len(receipt.get("passages", [])), "mainTrackId": main_id,
            **({'policy': policy, 'primaryCoverage': 'checked'} if primary_policy else {}),
            **({'musicalAudit': musical_audit} if regional_policy else {})}


def _notation(z, arr, receipt, source_rows, check):
    from .verification import _json
    notation = _json(z, arr["notation"], check)
    main = _json(z, source_rows[receipt["mainTrackId"]]["notationFile"], check)
    expected_header = {k: deepcopy(v) for k, v in main.items() if k != "measures"}
    for staff in expected_header["staves"]:
        staff["label"] = "Hybrid Lead"
    check.equal("hybrid_notation_header", arr["notation"], expected_header, {k: v for k, v in notation.items() if k != "measures"})
    check.equal("hybrid_notation_measures", arr["notation"], len(main["measures"]), len(notation["measures"]))
    for a, b in zip(main["measures"], notation["measures"]):
        check.equal("hybrid_notation_measure", arr["notation"], {k: v for k, v in a.items() if k != "staves"}, {k: v for k, v in b.items() if k != "staves"})
    originals = {tid: _json(z, row["notationFile"], check) for tid, row in source_rows.items() if row.get("notationFile")}
    next_voice = max((v['v'] for m in main['measures'] for s in m['staves'].values() for v in s['voices']), default=-1) + 1
    voices = {}
    selected_pairs = {id(p): {(sid, occurrence) for ref in p['events'] for sid in ref.get('sourceIds', [])
                              for occurrence in ref.get('occurrences', [])} for p in receipt['passages']}
    def selected(beat, passage, occurrence):
        within = beat['t'] >= passage['recordingStart'] - TOL and beat['t'] + beat.get('duration_seconds', 0) <= passage['recordingEnd'] + TOL and beat['t'] < passage['recordingEnd'] - 1e-7
        if not within or receipt.get('policy') != 'hybrid-lead-v3' or passage.get('priority') not in {'solo', 'lead'}:
            return within
        if beat.get('rest'):
            return (beat['t'] >= passage.get('recordingOwnedStart', passage['recordingStart'])-TOL
                    and beat['t'] + beat.get('duration_seconds', 0) <= passage.get('recordingOwnedEnd', passage['recordingEnd'])+TOL)
        pairs = selected_pairs[id(passage)]
        return ((beat.get('source_id'), occurrence) in pairs
                or any((n.get('source_id'), occurrence) in pairs for n in beat.get('notes', [])))
    for passage in receipt['passages']:
        for occurrence, m in enumerate(originals.get(passage['trackId'], {}).get('measures', []), start=1):
            for s in m['staves'].values():
                for voice in s['voices']:
                    key = (passage['trackId'], voice['v'])
                    if key not in voices and any(selected(b, passage, occurrence) for b in voice['beats']):
                        voices[key] = next_voice
                        next_voice += 1
    wanted = []
    main_kept = {(sid, occurrence) for ref in receipt.get('mainEvents', [])
                 for sid in ref.get('sourceIds', []) for occurrence in ref.get('occurrences', [])}
    main_removed = {(sid, occurrence) for ref in receipt.get('removedMain', [])
                    for sid in ref.get('sourceIds', []) for occurrence in ref.get('occurrences', [])}
    def retain_main(beat, occurrence):
        if receipt.get('policy') != 'hybrid-lead-v3':
            return not any(beat['t'] < r['recordingEnd']-1e-7 and beat['t']+beat.get('duration_seconds', 0) > r['recordingStart']+1e-7 for r in receipt.get('removedMain', []))
        identities = {(sid, occurrence) for sid in [beat.get('source_id'), *[n.get('source_id') for n in beat.get('notes', [])]] if sid is not None}
        return bool(beat.get('rest') or identities & main_kept or not identities & main_removed)
    for tid, row in source_rows.items():
        if not row.get("notationFile"):
            check.fail("hybrid_notation", "hybrid", "A contributing source has no supported notation.")
            continue
        original = _json(z, row["notationFile"], check)
        for occurrence, measure in enumerate(original["measures"], start=1):
            for staff in measure["staves"].values():
                for voice in staff["voices"]:
                    for beat in voice["beats"]:
                        keep_main = tid == receipt['mainTrackId'] and retain_main(beat, occurrence)
                        if keep_main or any(p["trackId"] == tid and selected(beat, p, occurrence) for p in receipt["passages"]):
                            number = voice['v'] if tid == receipt['mainTrackId'] else voices[(tid, voice['v'])]
                            wanted.append([measure["idx"], number, voice.get('source_id'), beat])
    actual = [[m["idx"], v['v'], v.get('source_id'), b] for m in notation["measures"] for s in m["staves"].values() for v in s["voices"] for b in v["beats"]]
    key = lambda item: json.dumps(item, sort_keys=True)
    check.equal("hybrid_notation", arr["notation"], sorted(wanted, key=key), sorted(actual, key=key))
