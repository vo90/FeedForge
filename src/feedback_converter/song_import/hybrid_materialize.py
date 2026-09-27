"""Materialize a source-aware plan by copying already retimed original events.

Original charts have passed the same endpoint/high-fret policies. Copying them
keeps all current and future technique fields and applies recording timing once.
"""
from copy import deepcopy
import hashlib
import json

from ..difficulty import ensure_difficulty
from ..chart_guidance import finalize as finalize_guidance
from ..verify_chart_guidance import validate_arrangement as check_guidance
from .audio import ImportFailure
from .hybrid_lead import NAME, POLICY


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def notation_for(plan, originals):
    main = originals[plan["mainTrackId"]].get("notation")
    if not main:
        return None, "The main guitar has source-only notation."
    if any(not originals[p["trackId"]].get("notation") for p in plan["passages"]):
        return None, "A contributing guitar has source-only notation."
    result = deepcopy(main)
    for staff in result["staves"]:
        staff["label"] = NAME
    # Separate source voices preserve original written rhythms and ties. Empty
    # donor voices are not added. Base rests remain valid in their own voice.
    used = {v["v"] for m in result["measures"] for s in m["staves"].values() for v in s["voices"]}
    next_voice = max(used, default=-1) + 1
    voice_ids = {}
    for p in plan["passages"]:
        notation = originals[p["trackId"]]["notation"]
        for target, source in zip(result["measures"], notation["measures"]):
            for staff_id, staff in source["staves"].items():
                for voice in staff["voices"]:
                    beats = [deepcopy(b) for b in voice["beats"] if b["t"] >= p["recordingStart"] - 1.1e-6
                             and b["t"] + b.get("duration_seconds", 0) <= p["recordingEnd"] + 1.1e-6
                             and b["t"] < p["recordingEnd"] - 1e-7]
                    if not beats:
                        continue
                    key = (p["trackId"], voice["v"])
                    if key not in voice_ids:
                        voice_ids[key] = next_voice
                        next_voice += 1
                    voices = target["staves"][staff_id]["voices"]
                    entry = next((v for v in voices if v["v"] == voice_ids[key]), None)
                    if entry is None:
                        entry = {**deepcopy(voice), "v": voice_ids[key], "beats": []}
                        voices.append(entry)
                    entry["beats"].extend(beats)
    return result, None


def materialize(plan, originals, options, source_hash, audio_hash, duration, generate_difficulty=False):
    base = originals[plan["mainTrackId"]]
    chart = deepcopy(base["chart"])
    chart["name"] = NAME
    chart.pop("phrases", None)
    chart.pop("difficulty_provenance", None)
    offsets = {plan["mainTrackId"]: 0}
    for p in plan["passages"]:
        original = originals[p["trackId"]]
        if p["trackId"] not in offsets:
            offsets[p["trackId"]] = len(chart["templates"])
            chart["templates"].extend(deepcopy(original["chart"]["templates"]))
        for ref in p["events"]:
            # Endpoint omission may change array indices. A missing event makes
            # the entire passage ineligible; never keep a cropped remainder.
            key = (ref["kind"], ref["index"])
            if key not in original["eventIndices"]:
                raise ImportFailure("hybrid_failed", "A selected passage changed during endpoint preparation.")
            index = original["eventIndices"][key]
            ref["sourceIndex"] = ref["index"]
            ref["index"] = index
            event = deepcopy(original["chart"][ref["kind"]][index])
            if ref["kind"] == "chords":
                event["id"] += offsets[p["trackId"]]
            chart[ref["kind"]].append(event)
    for key in ("notes", "chords"):
        chart[key].sort(key=lambda e: e["t"])
    # The base's generated positions no longer describe the completed Hybrid.
    # Regenerate only fields with intact ownership, before all final hashes.
    finalize_guidance(chart, regenerate=True)
    if generate_difficulty:
        ensure_difficulty(chart, duration=duration)
    guidance_errors = check_guidance(chart, duration=duration)
    if guidance_errors:
        raise ImportFailure("guidance_failed", "Hybrid guidance failed validation: " + guidance_errors[0])
    notation, notation_reason = notation_for(plan, originals)
    ident = "hybrid-lead-" + digest((source_hash + ":" + plan["mainTrackId"]).encode())[:20]
    sources = []
    for track_id in dict.fromkeys([plan["mainTrackId"], *(p["trackId"] for p in plan["passages"])]):
        row = originals[track_id]
        sources.append({"trackId": track_id, "arrangementId": row["manifest"]["id"], "file": row["manifest"]["file"],
                        "chartSha256": digest(encode(row["chart"])), **row["identity"],
                        **({"notationFile": row["manifest"]["notation"], "notationSha256": digest(encode(row["notation"]))} if row.get("notation") else {})})
    receipt = {**deepcopy(plan), "version": 1, "policy": POLICY, "arrangementId": ident,
               "sourceSha256": source_hash, "audioSha256": audio_hash, "options": deepcopy(options),
               "sources": sources, "chartSha256": digest(encode(chart)),
               "notationStatus": "composed" if notation else "source_only", "notationReason": notation_reason}
    if notation:
        receipt["notationSha256"] = digest(encode(notation))
    manifest = {"id": ident, "name": NAME, "file": f"arrangements/{ident}.json", "type": "lead",
                "tuning": deepcopy(base["manifest"]["tuning"]), "capo": base["manifest"]["capo"],
                "event_count": len(chart["notes"]) + len(chart["chords"]),
                "note_count": len(chart["notes"]) + sum(len(c["notes"]) for c in chart["chords"]),
                "derived": {"kind": POLICY, "receipt": "import/hybrid-lead.json"}}
    if notation:
        manifest["notation"] = f"notation/{ident}.json"
    return chart, notation, manifest, receipt
