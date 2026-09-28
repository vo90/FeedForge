import hashlib
import json
from zipfile import ZipFile

import pytest
import yaml

from feedback_converter.song_import.verification import verify_import
from feedback_converter.chart_guidance import POLICY
from test_song_import_verification import example, write
from test_songsterr_hybrid_lead import build, song


def hash_json(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def known_answer():
    """Literal positions, not the generator's answer used as its own oracle."""
    source, package = example()
    c = package["chart.json"]
    c.update(anchors=[{"time": 0, "fret": 3, "width": 5}, {"time": 2.5, "fret": 21, "width": 4}], handshapes=[])
    c["ext"] = {"chartGuidance": {"policy": POLICY, "sourceAuthored": False,
        "fields": ["anchors", "handshapes"], "slidePolicy": "known-corridor", "fingeringAssessed": False,
        "wideAnchorCount": 1, "handshapeEligibleChords": 0, "handshapeSkippedChords": 0,
        "musicSha256": hash_json({k: c[k] for k in ("tuning", "capo", "notes", "chords", "templates")}),
        "guidanceSha256": hash_json({k: c[k] for k in ("anchors", "handshapes")})}}
    package["manifest.yaml"]["song_import"] = {"chartGuidancePolicy": POLICY}
    return source, package


def test_hand_written_guidance_archive_passes_source_and_presentation_checks(tmp_path):
    source, package = known_answer()
    path, archive = write(tmp_path, source, package)
    result = verify_import(path, archive, {"offset": 1, "scale": 1}, guidance_policy=POLICY)
    assert result["status"] == "passed", result
    assert result["chartGuidance"] == {"policy": POLICY, "status": "passed", "arrangements": 1, "sourceAuthored": False}


@pytest.mark.parametrize("fault", ["missing_policy", "unknown_policy", "missing_proof", "missing_anchors", "stale_events", "rehashed_bad_coverage", "changed_ownership", "all_guidance_removed"])
def test_archive_cannot_claim_completion_after_guidance_tampering(tmp_path, fault):
    source, package = known_answer()
    c, recipe = package["chart.json"], package["manifest.yaml"]["song_import"]
    if fault in {"missing_policy", "all_guidance_removed"}: recipe.clear()
    if fault == "unknown_policy": recipe["chartGuidancePolicy"] += "-unknown"
    if fault in {"missing_proof", "all_guidance_removed"}: c.pop("ext")
    if fault in {"missing_anchors", "all_guidance_removed"}: c["anchors"] = []
    if fault == "stale_events": c["notes"][0]["f"] = 4
    if fault == "rehashed_bad_coverage":
        c["anchors"][1]["fret"] = 3
        c["ext"]["chartGuidance"]["guidanceSha256"] = hash_json({k: c[k] for k in ("anchors", "handshapes")})
    if fault == "changed_ownership":
        c["ext"]["chartGuidance"].update(fields=["handshapes"], guidanceSha256=hash_json({"handshapes": []}))
    path, archive = write(tmp_path, source, package)
    result = verify_import(path, archive, {"offset": 1, "scale": 1}, guidance_policy=POLICY)
    assert result["status"] == "failed", result


def test_historical_package_is_readable_but_cannot_pass_current_completion(tmp_path):
    source, package = example()
    path, archive = write(tmp_path, source, package)
    result = verify_import(path, archive, {"offset": 1, "scale": 1})
    assert result["status"] == "passed", result
    assert "chartGuidance" not in result
    assert verify_import(path, archive, {"offset": 1, "scale": 1}, guidance_policy=POLICY)["status"] == "failed"


@pytest.mark.parametrize("difficulty", [False, True])
def test_hybrid_donor_guidance_and_hashes_are_finalized_before_publication(tmp_path, difficulty):
    doc = song()
    doc["parts"][1]["measures"][1]["voices"][0]["beats"][0]["notes"][0]["fret"] = 19
    source, _, _, alignment, archive, report = build(tmp_path, doc, mapped=True, difficulty=difficulty)
    assert report["status"] == "passed", report
    assert report["chartGuidance"]["arrangements"] == 4
    with ZipFile(archive) as z:
        manifest = yaml.safe_load(z.read("manifest.yaml"))
        derived = manifest["arrangements"][-1]
        chart = json.loads(z.read(derived["file"]))
        donor = next(n for n in chart["notes"] if n["f"] == 19)
        active = next(a for a in reversed(chart["anchors"]) if a["time"] <= donor["t"])
        assert active["fret"] <= 19 < active["fret"] + active["width"]
        members = {n: z.read(n) for n in z.namelist()}
    chart["anchors"] = [{"time": 0, "fret": 1, "width": 4}]
    members[derived["file"]] = json.dumps(chart).encode()
    tampered = tmp_path / "tampered.feedpak"
    with ZipFile(tampered, "w") as z:
        for name, contents in members.items():
            z.writestr(name, contents)
    failed = verify_import(source, tampered, alignment, guidance_policy=POLICY)
    assert failed["status"] == "failed"
    assert any(e["code"] == "chart_guidance" for e in failed["errors"])
