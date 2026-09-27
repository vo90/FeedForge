"""Conservative, deterministic whole-passage composition in musical time.

No notes are generated, retimed, shortened, or re-fingered here. The output is a
plan referencing original performed events; materialization happens separately.
"""
from copy import deepcopy
import json
import math
import re
from statistics import median

from .audio import ImportFailure
from .alignment import map_time
from .hybrid_context import Clock

POLICY = "hybrid-lead-v1"
NAME = "Hybrid Lead"
EPS = 1e-7


def normalize_options(value=None):
    if value is None:
        return {"enabled": False}
    if not isinstance(value, dict) or not isinstance(value.get("enabled", False), bool):
        raise ValueError("Hybrid Lead options must include a boolean enabled setting.")
    if set(value) - {"enabled", "mainTrackId", "excludedTrackIds", "preferredTrackIds", "sourceSha256", "reviewSources"}:
        raise ValueError("Unknown Hybrid Lead option.")
    if not value.get("enabled"):
        return {"enabled": False}
    out = {"enabled": True}
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
    ids = {t["id"] for t in guitars}
    if options.get("sourceSha256") and options["sourceSha256"] != source_hash:
        raise ImportFailure("hybrid_choice_stale", "The tab changed. Choose the main guitar again.")
    if (set(options.get("excludedTrackIds", [])) | set(options.get("preferredTrackIds", []))) - ids:
        raise ImportFailure("hybrid_choice_stale", "A selected guitar is no longer available in this tab.")
    if options.get("mainTrackId"):
        if options["mainTrackId"] not in ids:
            raise ImportFailure("hybrid_choice_stale", "The chosen main guitar is no longer available.")
        return options["mainTrackId"]
    lead = [t for t in guitars if t.get("role") == "lead" and not re.search(r"\b(solo|delay|echo|effect)\b", t["name"], re.I)]
    suggested = guitars[0]["id"] if len(guitars) == 1 else lead[0]["id"] if len(lead) == 1 else None
    if suggested and not options.get("reviewSources"):
        return suggested
    raise ImportFailure("awaiting_main_choice", "Choose the guitar that Hybrid Lead should follow.", {
        "sourceSha256": source_hash, "suggestedMainTrackId": suggested,
        "tracks": [{k: t[k] for k in ("id", "name", "role", "tuning", "capo")} for t in guitars]})


def union(intervals):
    out = []
    for a, b in sorted(intervals):
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
    for grace in (b for b in beats if b["grace"]):
        following = next((b for b in beats if b["voice"] == grace["voice"] and not b["rest"] and not b["grace"] and b["start"] >= grace["end"] - EPS), None)
        if following:
            linked = set(grace["noteIds"] + following["noteIds"])
            touched = [r for r in rows if linked.intersection(r["sourceIds"]) and r["start"] < following["end"] + EPS and r["end"] > grace["start"] - EPS]
            for row in touched:
                row["start"] = min(row["start"], grace["start"])
                row["end"] = max(row["end"], following["end"])
    # A destination hammer/pull or pitched slide needs its predecessor even if
    # the source has a short sounding gap. Grace pickups are already in slots.
    per_string = {}
    for row in sorted(rows, key=lambda r: r["start"]):
        for note in row["notes"]:
            prior = per_string.get(note["s"])
            if prior and (note.get("ho") or note.get("po") or note.get("ln") or
                          any(n.get("sl") is not None or n.get("slu") for n in prior["notes"] if n["s"] == note["s"])):
                prior["end"] = max(prior["end"], row["end"])
                row["start"] = min(row["start"], prior["start"])
            per_string[note["s"]] = row
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


def passages(track, context, timeline, clock):
    rows = event_rows(track, context, clock)
    if not rows:
        return []
    occupied_rows = occupied(track, context, clock)
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
        def hand_position(row):
            frets = [n["f"] for n in row["notes"] if 0 < n["f"] <= 24]
            return median(frets) if frets else None
        out.append({"trackId": track["id"], "start": lo, "end": hi,
                    "boundaries": [boundaries[a], boundaries[b]],
                    "boundaryQuarters": [a, b],
                    "entryFret": hand_position(events[0]), "exitFret": hand_position(events[-1]),
                    "events": [{k: r[k] for k in ("kind", "index", "sourceIds", "occurrences")} for r in events]})
    return out


def plan(performance, options, main_id, alignment, audio_duration):
    context = performance.get("compositionContext")
    if not context or context.get("version") != 1:
        raise ImportFailure("hybrid_failed", "Hybrid Lead needs the performed written score context.")
    clock = Clock(context["timeline"])
    end = clock.quarter(performance["duration"])
    by_id = {t["id"]: t for t in performance["tracks"]}
    main = by_id[main_id]
    protected = occupied(main, context["tracks"][main_id], clock)

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
    excluded, candidates = [], []
    main_signature = _fingerprint(event_rows(main, context["tracks"][main_id], clock), 0, end, clock)
    signatures = {main_signature}
    preferred = options.get("preferredTrackIds", [])
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
        accepted = [p for p in possible if any(lo - EPS <= p["start"] and p["end"] <= hi + EPS for lo, hi in gaps)
                    and map_time(alignment, clock.seconds(p["end"]), allow_negative=True) <= audio_duration + EPS]
        candidates.extend(accepted)
        if not accepted:
            excluded.append({"trackId": ident, "reason": "no_complete_passage_fits", "candidateCount": len(possible)})
    # Explicit source priorities are lexicographic. Otherwise maximize passage
    # coverage; within one beat of that optimum compare confidence/continuity.
    # Keep Pareto states per ending donor, never a fuzzy pairwise comparator.
    candidates.sort(key=lambda p: (p["end"], p["start"], p["trackId"]))
    if len(candidates) > 20000:
        raise ImportFailure("hybrid_failed", "This tab exceeds the Hybrid Lead passage limit.")
    def select_gap(candidates):
        states = [{"chosen": [], "end": -math.inf, "coverage": 0.0, "preference": tuple(0.0 for _ in preferred), "switches": 0, "confidence": 0, "comfort": 0.0}]
        for i, p in enumerate(candidates):
            extensions = []
            for state in states:
                last = candidates[state["chosen"][-1]] if state["chosen"] else None
                same = last and last["trackId"] == p["trackId"]
                distance = 0 if same or not last else max(guard(last["end"], 1), guard(p["start"], -1))
                if state["end"] + distance > p["start"] + EPS:
                    continue
                coverage = p["end"] - p["start"]
                preference = list(state["preference"])
                if p["trackId"] in preferred:
                    rank = preferred.index(p["trackId"])
                    preference[rank] = round(preference[rank] + coverage, 8)
                movement = 0.0 if not last or last["exitFret"] is None or p["entryFret"] is None else abs(last["exitFret"] - p["entryFret"])
                extensions.append({"chosen": state["chosen"] + [i], "end": p["end"], "coverage": state["coverage"] + coverage,
                                   "preference": tuple(preference), "comfort": state["comfort"] + movement,
                                   "switches": state["switches"] + int(last is not None and not same),
                                   "confidence": state["confidence"] + sum(b in {"song", "rest", "section"} for b in p["boundaries"])})
            # A later-ending state cannot dominate an earlier one. Remove only
            # states with the same last donor and no benefit in any objective.
            pool = states + extensions
            states = []
            groups = {}
            for s in sorted(pool, key=lambda s: (s["end"], -s["coverage"], tuple(-x for x in s["preference"]), s["switches"], -s["confidence"], s["comfort"], s["chosen"])):
                last = candidates[s["chosen"][-1]] if s["chosen"] else None
                donor = (last["trackId"], last["exitFret"]) if last else None
                frontier = groups.setdefault(donor, [])
                if any(t["coverage"] >= s["coverage"] - EPS and t["preference"] >= s["preference"] and
                       t["switches"] <= s["switches"] and t["confidence"] >= s["confidence"] and t["comfort"] <= s["comfort"] for t in frontier):
                    continue
                frontier.append(s)
                states.append(s)
            if len(states) > 10000:
                raise ImportFailure("hybrid_failed", "Hybrid Lead has too many competing passage plans. Exclude some supplementary guitars and retry.")
        best_preference = max(s["preference"] for s in states)
        preferred_states = [s for s in states if s["preference"] == best_preference]
        best = max(s["coverage"] for s in preferred_states)
        eligible = [s for s in preferred_states if s["coverage"] >= best - 1 - EPS]
        chosen = min(eligible, key=lambda s: (s["switches"], -s["confidence"], s["comfort"], -s["coverage"], s["chosen"]))
        return chosen["chosen"], best

    chosen_indices, best = [], 0.0
    for lo, hi in gaps:
        indices = [i for i, p in enumerate(candidates) if lo - EPS <= p["start"] and p["end"] <= hi + EPS]
        selected_indices, coverage = select_gap([candidates[i] for i in indices])
        chosen_indices.extend(indices[i] for i in selected_indices)
        best += coverage
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
            "selection": {"algorithm": "per-gap-pareto-interval-plan-v1", "bestQuarterBeats": best, "indifferenceQuarterBeats": 1,
                          "priority": "explicit-source-order, coverage-band, fewer-switches, boundary-confidence, fret-movement, coverage, stable-id"},
            "unselected": [{k: p[k] for k in ("trackId", "start", "end", "boundaries")} for i, p in enumerate(candidates) if i not in chosen_set]}
