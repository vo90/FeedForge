"""Independent omission oracle over the separately reconstructed source events.

Deliberately does not import the exporter or its high-fret projection.
"""
import hashlib
import json


def reconstruct(reference, mapped):
    receipt = {"version": 1, "policy": "omit-unsupported-frets-v1", "maxFret": 24,
               "notes": [], "links": [], "excludedTracks": [], "notationWithheld": []}
    for original, target in zip(reference["parts"], mapped["parts"]):
        part = original["source"]
        rejected = {}
        def position(note):
            return {"trackId": part.id, "scoreStart": round(note["t"], 6),
                    "scoreDuration": round(note.get("sus", 0), 6), "string": note["s"], "fret": note["f"]}
        for index, item in enumerate(original["notes"]):
            n = item["note"]
            if n.get("pick_scrape_marks"):
                continue
            why = ("fret_above_24" if type(n["f"]) is int and n["f"] in range(25, 49) else
                   "slide_target_above_24" if any(type(n.get(k)) is int and n[k] in range(25, 49) for k in ("sl", "slu")) else None)
            if why:
                rejected[index] = why
                receipt["notes"].append({**position(n), "reason": why, **{k: n[k] for k in ("sl", "slu") if k in n}})
        if not rejected:
            continue
        previous = {}
        for index, item in enumerate(original["notes"]):
            n = item["note"]
            before = previous.get(n["s"])
            if index in rejected and before is not None and before not in rejected:
                prior = original["notes"][before]["note"]
                if prior.get("ln"):
                    receipt["links"].append(position(prior))
                    target["notes"][before]["note"].pop("ln", None)
            previous[n["s"]] = index
        target["sync_notes"] = target["notes"]
        target["notes"] = [n for i, n in enumerate(target["notes"]) if i not in rejected]
        target["high_fret_omissions"] = True
        if not part.notation_unavailable and not part.unpitched_mutes:
            receipt["notationWithheld"].append(part.id)
        if not target["notes"]:
            receipt["excludedTracks"].append(part.id)
    for key in ("notes", "links"):
        receipt[key].sort(key=lambda n: (n["trackId"], n["scoreStart"], n["string"], n["fret"]))
    for key in ("excludedTracks", "notationWithheld"):
        receipt[key].sort()
    return receipt


def check_receipt(expected_receipt, actual, check):
    """Exact identities/counts, with the existing one-microsecond time tolerance."""
    if not isinstance(actual, dict) or set(actual) != set(expected_receipt):
        check.fail("omission_receipt", "import/high-fret-omissions", "Missing or unexpected omission receipt fields.")
        return
    encode = lambda value: json.dumps(value, sort_keys=True, ensure_ascii=False)
    for key in expected_receipt:
        if key not in {"notes", "links"}:
            if encode(expected_receipt[key]) != encode(actual[key]):
                check.fail("omission_receipt", "import/high-fret-omissions/" + key, "Omission receipt header differs from the source-derived policy.")
            continue
        rows = actual[key]
        wanted = expected_receipt[key]
        if not isinstance(rows, list) or len(rows) != len(wanted):
            check.fail("omission_receipt", "import/high-fret-omissions/" + key, "Omission receipt event count differs from the original source.")
            continue
        for index, (a, b) in enumerate(zip(wanted, rows)):
            location = f"import/high-fret-omissions/{key}/{index}"
            if not isinstance(b, dict) or set(a) != set(b):
                check.fail("omission_receipt", location, "Omission event fields differ from the source-derived event.")
                continue
            for field in a:
                if field in {"scoreStart", "scoreDuration"}:
                    check.near("omission_time", location + "/" + field, a[field], b[field])
                elif encode(a[field]) != encode(b[field]):
                    check.fail("omission_receipt", location + "/" + field, "Omission event identity differs from the original source.")


def verify(source, wanted, source_path, recipe, archive, check, read_json):
    if source.format != "songsterr":
        if recipe.get("highFretOmissionsFile") or recipe.get("omissions"):
            check.fail("omission_contract", "import", "The high-fret policy applies only to Songsterr imports.")
        return None
    has_high = any(not n["note"].get("pick_scrape_marks") and
                   any(type(n["note"].get(k)) is int and 24 < n["note"][k] <= 48 for k in ("f", "sl", "slu"))
                   for p in wanted["parts"] for n in p["notes"])
    filename = recipe.get("highFretOmissionsFile")
    if not has_high:
        if filename or recipe.get("omissions") or "import/high-fret-omissions.json" in archive.namelist():
            check.fail("unexpected_omissions", "import", "High-fret omissions were declared for a source without them.")
        return None
    if recipe.get("preservationContract", 0) < 24:
        check.fail("omission_contract", "import", "This partial-chart policy requires Songsterr preservation contract 24.")
        return None
    from .verify_timeline import expected
    reference = expected(source, {"offset": 0, "scale": 1})
    receipt = reconstruct(reference, wanted)
    receipt["sourceSha256"] = hashlib.sha256(source_path.read_bytes()).hexdigest()
    check.equal("omission_file", "import", "import/high-fret-omissions.json", filename)
    if filename:
        stored = read_json(archive, filename, check)
        check_receipt(receipt, stored, check)
    summary = {"policy": "omit-unsupported-frets-v1", "omittedNotes": len(receipt["notes"]),
               "omittedSlideEvents": sum(n["reason"] == "slide_target_above_24" for n in receipt["notes"]),
               "clearedLinks": len(receipt["links"]), "excludedTracks": receipt["excludedTracks"],
               "notationWithheld": receipt["notationWithheld"]}
    for actual in (recipe.get("omissions"), recipe.get("coverage", {}).get("omissions")):
        check.equal("omission_summary", "manifest/song_import", summary, actual)
    return summary
