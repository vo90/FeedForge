"""Offline replay and stage comparison. These reports never authorize publication."""
from collections import Counter
from copy import deepcopy
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

from feedback_converter.song_import.compatibility import inspect_songsterr
from feedback_converter.song_import.diagnostics import diagnose_arrangements
from feedback_converter.song_import.model import ScoreImportError
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render, playback_order
from feedback_converter.song_import.verify_source import songsterr as independent_source, UnverifiedFeature
from feedback_converter.song_import.verify_timeline import expected

SCOPE = "Source timing only. Not an acquisition, audio alignment, Hybrid Lead or FeedPak acceptance result."
REFERENCE = Path(__file__).with_name("reference-manifest.json")
LIMIT = 80 * 1024 * 1024


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def source_trace(score):
    order = playback_order(score.measures)
    visits, start = [], Fraction(0)
    for index in order:
        visits.append((index, start)); start += score.measures[index].length
    return [{"index": int(t.id), "beats": [
        {"id": f"{mi}:{v.source_index if v.source_index is not None else vi}:{bi}",
         "quarter": str(b.position), "duration": str(b.duration)}
        for mi, voices in enumerate(t.written_bars) for vi, v in enumerate(voices) for bi, b in enumerate(v.beats)],
        "traversal": order, "notes": [
            {"id": ":".join(n.source_id.split(":")[2:]), "occurrence": occurrence,
             "string": len(t.tuning) - 1 - n.string, "fret": n.fret, "tie": n.tie,
             "baseQuarter": str(start + n.position), "offsetQuarter": str(n.attack_offset),
             "endQuarter": str(start + n.position + n.duration)}
            for occurrence, (mi, start) in enumerate(visits) for n in t.bars[mi]]} for t in score.tracks]


def evaluate(source):
    original = canonical_hash(source)
    report = inspect_songsterr(source)
    result = {"sourceSha256": original, "scope": SCOPE,
              "inventory": {"status": report["status"], "blocking": sorted({f["feature"] for f in report["findings"] if f["impact"] == "blocking"})}}
    for stage in ("converter", "independent"):
        try:
            if stage == "converter":
                score = parse(source)
                result["sourceTrace"] = source_trace(score)
                performance = render(score)
                count = sum(len(t["notes"]) + sum(len(c["notes"]) for c in t["chords"]) for t in performance["tracks"])
            else:
                performance = expected(independent_source(source), {"offset": 0, "scale": 1})
                count = sum(len(p["notes"]) for p in performance["parts"])
            result[stage] = {"status": "rendered", "events": count}
        except (ScoreImportError, UnverifiedFeature, ValueError) as exc:
            result[stage] = {"status": "blocked", "error": str(exc)}
    if canonical_hash(source) != original:
        raise AssertionError("Source mutated during assessment")
    if result["converter"]["status"] == "blocked":
        diagnose_arrangements(source, report)
        result["arrangements"] = report.get("arrangements", [])
    return result


def compare_preparation(actual, reference):
    """Compare the pre-repeat authored clock, not synthesized attacks or envelopes."""
    differences = []
    got = {p["index"]: p for p in actual.get("sourceTrace", [])}
    if len(got) != len(actual.get("sourceTrace", [])):
        differences.append({"code": "duplicate_converter_part"})
    if not reference.get("parts"):
        return [{"code": "reference_no_parts"}]
    seen_parts = set()
    for part in reference["parts"]:
        index = part["index"]
        if index in seen_parts:
            differences.append({"part": index, "code": "duplicate_reference_part"})
        seen_parts.add(index)
        if part["status"] != "executed":
            differences.append({"part": index, "code": "reference_error", "message": part.get("error")})
            continue
        if index not in got:
            differences.append({"part": index, "code": "converter_preparation_not_run"})
            continue
        local = got.pop(index)
        if local["traversal"] != part["traversal"]:
            differences.append({"part": index, "code": "traversal"})
        beats = {b["id"]: b for b in local["beats"]}
        if len(beats) != len(local["beats"]):
            differences.append({"part": index, "code": "duplicate_converter_beat"})
        for b in part["beats"]:
            a = beats.pop(b["id"], None)
            if a is None:
                differences.append({"part": index, "beat": b["id"], "code": "missing_beat"})
                continue
            for field in ("quarter", "duration"):
                # Public preparation uses IEEE doubles in quarter units. This is
                # numeric representation tolerance only, not MIDI tick rounding.
                if b[field] is None or abs(Fraction(a[field]) - Fraction(str(b[field]))) > Fraction(1, 100000000):
                    differences.append({"part": index, "beat": b["id"], "code": field,
                                        "converter": a[field], "reference": b[field]})
        differences.extend({"part": index, "beat": k, "code": "extra_beat"} for k in beats)
    differences.extend({"part": i, "code": "extra_part"} for i in got)
    return differences


def comparison_status(differences):
    unavailable = {"reference_error", "reference_no_parts", "converter_preparation_not_run"}
    if any(d["code"] not in unavailable for d in differences):
        return "different"
    return "not_tested" if differences else "matched"


def classify_preparation_differences(source, differences):
    """Existing whole-measure rest projection is distinct from the glyph clock.

    No note clocks or ordinary rest sequences qualify for this exception.
    Derive meter here from the raw document, not the converter's trace.
    """
    approved, unexplained = [], []
    for d in differences:
        qualifies = False
        if d['code'] == 'duration' and d.get('reference') == 4:
            mi, vi, bi = map(int, d['beat'].split(':'))
            measures = source['parts'][d['part']]['measures']
            meter = [4, 4]
            for measure in measures[:mi+1]:
                if measure.get('signature'): meter = measure['signature']
            beats = measures[mi]['voices'][vi]['beats']; b = beats[bi]
            qualifies = (len(beats) == 1 and b.get('rest') is True and b.get('type') == 1
                         and b.get('duration') == [1, 1] and not any(b.get(k) for k in ('dots','tuplet','graceNote'))
                         and b.get('notes') and all(n.get('rest') is True for n in b['notes'])
                         and Fraction(d['converter']) == Fraction(4*meter[0], meter[1]))
        if qualifies:
            approved.append({**d, 'policy': 'existing_whole_measure_rest_projection'})
        else:
            unexplained.append(d)
    return approved, unexplained


def _strum_tick_adjustment(beat, note_index, resolution):
    """Pinned worker's quantization error relative to the authored rational clock.

    This is development comparison policy, never an import timing tolerance.
    Only the explicitly qualified matrix forms are accepted here.
    """
    notes = beat["notes"]
    modern = beat.get("arpeggio") or beat.get("brushStroke")
    old = beat.get("upArpeggio") or beat.get("downArpeggio")
    if not modern and not old:
        return Fraction(0)
    if any(n.get(k) for n in notes for k in ("bend", "hp", "slide")):
        return Fraction(0)
    count = len(notes)
    if count < 2:
        return Fraction(0)
    if modern:
        cap = min(Fraction(modern["duration"]), min(960, Fraction(*beat["duration"]) * 1920))
        step = cap * resolution / (480 * count)
        tick_step = (cap * resolution / 480 // 1) // count
        shift = Fraction(str(modern["shift"]))
        direction = modern["direction"]
    else:
        step = Fraction(4 * resolution, 13 * 2**(8-old))
        tick_step = step // 1
        shift = 100
        direction = "up" if beat.get("upArpeggio") else "down"
    rank = sorted(range(count), key=lambda i: notes[i]["string"]).index(note_index)
    rank = rank if direction == "up" else count - 1 - rank
    ideal = step * rank - step * (count - 1) * (100-shift) / 100
    ticks = tick_step * rank - (tick_step * (count-1) * (100-shift) / 100 // 1)
    return ticks - ideal


def compare_authored_events(actual, reference, source):
    """First scheduling stage only, before ties, let-ring and synth envelopes.

    Qualified for the synthetic timing matrix, not arbitrary pitch effects.
    Apply only the explicitly calculated strum tick quantization difference;
    the reference releases at the last tick before the authored end.
    """
    differences = []
    for part in reference.get("parts", []):
        own = next((p for p in actual.get("sourceTrace", []) if p["index"] == part["index"]), None)
        if own is None or "authoredEvents" not in part:
            differences.append({"part": part["index"], "code": "authored_events_not_tested"}); continue
        events = {(e["id"], e["occurrence"]): e for e in own["notes"]}
        if len(events) != len(own["notes"]):
            differences.append({"part": part["index"], "code": "duplicate_source_event"})
        resolution = part["tpqn"]
        for r in part["authoredEvents"]:
            key = (r["id"], r["occurrence"])
            e = events.pop(key, None)
            where = {"part": part["index"], "source": r["id"], "occurrence": r["occurrence"]}
            if e is None:
                differences.append({**where, "code": "unaccounted_reference_event"}); continue
            mi, vi, bi, ni = map(int, e["id"].split(":"))
            beat = source["parts"][part["index"]]["measures"][mi]["voices"][vi]["beats"][bi]
            adjustment = _strum_tick_adjustment(beat, ni, resolution)
            expected = {"string": e["string"], "fret": e["fret"], "tie": e["tie"],
                        "attackTick": (Fraction(e["baseQuarter"]) + Fraction(e["offsetQuarter"]))*resolution + adjustment,
                        "endTick": Fraction(e["endQuarter"])*resolution - 1}
            for field, value in expected.items():
                # Only binary floating representation at the reference tick scale.
                differs = (r.get(field) is None or abs(Fraction(str(r[field]))-value) > Fraction(1, 100000000)) if field.endswith('Tick') else r.get(field) != value
                if differs:
                    differences.append({**where, "code": field, "converter": str(value), "reference": r.get(field)})
        differences.extend({"part": part["index"], "source": k[0], "occurrence": k[1], "code": "unaccounted_converter_event"} for k in events)
    return differences or ([] if reference.get("parts") else [{"code": "authored_events_not_tested"}])


def reference_rows(rows, worker, node, trace=False):
    with tempfile.TemporaryDirectory(prefix="feedforge-reference-") as directory:
        root = Path(directory)
        data = {"cases": rows, "trace": trace, "profile": "authored"}
        (root / "input.json").write_text(json.dumps(data), encoding="utf-8")
        try:
            p = subprocess.run([node, "--max-old-space-size=512", str(Path(__file__).with_name("reference.cjs")),
                                str(worker), str(root / "input.json"), str(root / "output.json")],
                               capture_output=True, text=True, timeout=max(30, len(rows) * 15))
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Reference timed out; comparison not tested") from exc
        if p.returncode:
            raise RuntimeError("Reference unavailable: " + p.stderr[:1000])
        value = json.loads((root / "output.json").read_text(encoding="utf-8"))
        pin = json.loads(REFERENCE.read_text())
        if value["referenceSha256"] != pin["sha256"]:
            raise ValueError("Reference result identity mismatch")
        if len(value["cases"]) != len(rows) or [r["id"] for r in value["cases"]] != [r["id"] for r in rows]:
            raise ValueError("Reference case identity mismatch")
        if any(a.get("sourceSha256") != b.get("sourceSha256") for a, b in zip(value["cases"], rows)):
            raise ValueError("Reference source identity mismatch")
        return value


def run(rows, *, worker=None, node="node", trace=False):
    results = []
    for row in rows:
        result = {"id": row["id"], "family": row.get("family", "corpus"), **evaluate(row["source"])}
        if worker:
            reference = reference_rows([{**row, "sourceSha256": result["sourceSha256"]}], worker, node, trace)["cases"][0]
            result["reference"] = reference
            result["preparationDifferences"] = compare_preparation(result, reference)
            result['approvedPreparationDifferences'], result['preparationDifferences'] = classify_preparation_differences(row['source'], result['preparationDifferences'])
            result["preparationStatus"] = comparison_status(result["preparationDifferences"])
            if row.get("family", "").startswith("timing."):
                result["authoredEventDifferences"] = compare_authored_events(result, reference, row["source"])
                result["authoredEventStatus"] = "different" if result["authoredEventDifferences"] else "matched"
            else:
                result["authoredEventStatus"] = "not_tested_effect_policies_not_qualified"
            if not trace:
                result["reference"] = {"id": reference["id"], "parts": [
                    {"index": p["index"], "status": p["status"], "error": p.get("error"),
                     "events": len(p.get("events", [])),
                     "hiddenEvents": sum(n["hidden"] for n in p.get("events", []))}
                    for p in reference["parts"]]}
        else:
            result["reference"] = {"status": "not_run"}
            result["preparationStatus"] = "not_tested"
        results.append(result)
    return {"version": 1, "scope": SCOPE, "cases": results,
            "counts": dict(Counter(r["converter"]["status"] for r in results)),
            "referenceCompared": sum("preparationDifferences" in r for r in results),
            "preparationCounts": dict(Counter(r["preparationStatus"] for r in results)),
            "unexplainedPreparationCases": sum(r["preparationStatus"] == "different" for r in results)}


def manifest_from_evidence(imports, evidence):
    """Freeze exact source objects; bounded job history is not the corpus."""
    rows = []
    for file in sorted(Path(imports).glob("*.json")):
        value = json.loads(file.read_text(encoding="utf-8-sig"))
        row = {"id": str(value.get("rank", file.stem)), "songId": str(value["id"]),
               "acquisition": value.get("status"), "source": None}
        for attempt in reversed(value.get("attempts", [])):
            ref = (attempt.get("rawJob", {}).get("evidence") or {}).get("id")
            if not ref or len(ref) != 64 or any(c not in "0123456789abcdef" for c in ref):
                continue
            record = Path(evidence) / "records" / (ref + ".json")
            if not record.is_file():
                continue
            record_bytes = record.read_bytes()
            if hashlib.sha256(record_bytes).hexdigest() != ref:
                raise ValueError("Evidence record hash mismatch")
            h = json.loads(record_bytes).get("objects", {}).get("source")
            if not h or len(h) != 64 or any(c not in "0123456789abcdef" for c in h):
                continue
            path = Path(evidence) / "objects" / h
            if path.is_file():
                if path.stat().st_size > LIMIT or hashlib.sha256(path.read_bytes()).hexdigest() != h:
                    raise ValueError("Evidence source hash mismatch")
                doc = json.loads(path.read_text(encoding="utf-8-sig"))
                if str(doc.get("songId")) != row["songId"]:
                    raise ValueError("Evidence song identity mismatch")
                row.update(source=str(path.resolve()), sha256=h, revisionId=str(doc["revisionId"]), evidenceId=ref)
                break
        rows.append(row)
    return {"version": 1, "selection": "frozen_original_attempts", "rows": rows}


def load_manifest(path):
    manifest = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    seen = set()
    for row in manifest["rows"]:
        if row["id"] in seen:
            raise ValueError("Duplicate corpus ID")
        seen.add(row["id"])
        if row["source"] is None:
            yield row, None
            continue
        p = Path(row["source"])
        if p.stat().st_size > LIMIT:
            raise ValueError("Source limit")
        data = p.read_bytes()
        if hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise ValueError("Corpus source changed")
        doc = json.loads(data.decode("utf-8-sig"))
        if str(doc["songId"]) != row["songId"] or str(doc["revisionId"]) != row["revisionId"]:
            raise ValueError("Corpus identity changed")
        yield row, doc


def minimize(source, reproduces, limit=100):
    """Reduce independent measures only when both sides still reproduce the predicate.

    Navigation and cross-bar context are deliberately retained. This reducer
    cannot repair an invalid fragment to manufacture a smaller failure.
    """
    result = deepcopy(source)
    if len(result["parts"]) != 1:
        return result
    part = result["parts"][0]
    if any(any(m.get(k) for k in ("repeat", "repeatStart", "alternateEnding", "direction", "directions", "jump", "pickup")) for m in part["measures"]):
        return result
    automations = part.get("automations", {})
    if (set(automations) - {"tempo"} or len(automations.get("tempo", [])) > 1
            or any(t.get("measure", 0) != 0 or t.get("position", 0) != 0 for t in automations.get("tempo", []))):
        return result
    attempts = 0
    for index in range(len(part["measures"])-1, -1, -1):
        if len(result["parts"][0]["measures"]) <= 1 or attempts >= limit:
            break
        candidate = deepcopy(result); del candidate["parts"][0]["measures"][index]; attempts += 1
        if reproduces(candidate):
            result = candidate
    return result
