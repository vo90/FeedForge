"""Small durable comparison evidence; hashes are indexes, not musical verdicts."""
from fractions import Fraction
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import platform


def fingerprint(value):
    def encode(item):
        if isinstance(item, Fraction):
            return {"exactFraction": [item.numerator, item.denominator]}
        raise TypeError(f"Unsupported evidence value: {type(item).__name__}")
    data = json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False, default=encode)
    return hashlib.sha256(data.encode()).hexdigest()


@lru_cache(maxsize=1)
def implementation_identity():
    root = Path(__file__).resolve().parents[2]
    files = sorted((root / "src/feedback_converter/song_import").glob("*.py"))
    files += sorted(Path(__file__).parent.glob("*.py"))
    files += sorted(Path(__file__).parent.glob("*.cjs"))
    return {"python": platform.python_version(), "filesSha256": fingerprint({
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in files})}


def reference_identity(manifest):
    data = json.loads(manifest.read_text())
    return {"assetSha256": data["sha256"],
            "manifestSha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
            "profile": "authored", "defaultProfile": "authored",
            "additionalProfiles": {"legacy-brush-authored-v1": {
                "policy": "songsterr-legacy-brush-direction-swap-v1", "version": 1,
                "synth": "fluidsynth", "useRSE": False, "autoFixJson": True, "humanize": False}},
            "qualification": data.get("qualification", {}),
            "generatedEventQualification": data.get("generatedEventQualification", {})}


def compact_reference(reference):
    """Retain stage signatures before discarding the large optional trace."""
    return {"id": reference["id"], "parts": [
        {"index": p["index"], "status": p["status"], "error": p.get("error"),
         "tpqn": p.get("tpqn"), "profile": p.get("profile"),
         **({"normalization": p["normalization"]} if "normalization" in p else {}),
         "events": len(p.get("events", [])),
         "hiddenEvents": sum(n["hidden"] for n in p.get("events", [])),
         **({"generatedEventTraceVersion": p["generatedEventTrace"]["version"],
             "scheduledEvents": len(p["generatedEventTrace"]["scheduled"])}
            if "generatedEventTrace" in p else {}),
         "stageSha256": {k: fingerprint(p[k]) for k in
                         ("beats", "traversal", "authoredEvents", "events", "generatedEventTrace") if k in p}}
        for p in reference["parts"]]}


def compare_reports(before, after):
    """Compare like-for-like runs, without blessing a changed reference or chart.

    A timing match says nothing about unqualified effects or acoustic alignment.
    Old reports lacking stage evidence are incomplete, never an unchanged pass.
    """
    issues, changes = [], []
    if before.get("scope") != after.get("scope"):
        issues.append({"code": "different_scope"})
    for label, report in (("before", before), ("after", after)):
        if not report.get("implementation") or not report.get("referenceIdentity"):
            issues.append({"code": "missing_run_identity", "report": label})
    indexes = []
    for label, report in (("before", before), ("after", after)):
        rows = report.get("cases", [])
        index = {r["id"]: r for r in rows}
        if not rows or len(index) != len(rows):
            issues.append({"code": "empty_or_duplicate_cases", "report": label})
        indexes.append(index)
    left, right = indexes
    if left.keys() != right.keys():
        issues.append({"code": "different_case_set", "onlyBefore": sorted(left.keys()-right.keys()),
                       "onlyAfter": sorted(right.keys()-left.keys())})
    for key in sorted(left.keys() & right.keys()):
        a, b = left[key], right[key]
        if not a.get("sourceSha256") or a.get("sourceSha256") != b.get("sourceSha256"):
            issues.append({"id": key, "code": "different_source"}); continue
        generated_required = any("generatedEventTrace" in p.get("stageSha256", {})
                                 for row in (a, b) for p in row.get("reference", {}).get("parts", []))
        for label, row in (("before", a), ("after", b)):
            parts = row.get("reference", {}).get("parts", [])
            if generated_required and any(p.get("generatedEventTraceVersion") != 1 or
                    "generatedEventTrace" not in p.get("stageSha256", {}) for p in parts):
                issues.append({"id": key, "code": "incomplete_generated_event_evidence", "report": label})
            if (row.get("preparationStatus") == "not_tested" or not parts or
                    any(p.get("status") != "executed" or
                        not {"beats", "traversal", "authoredEvents", "events"}.issubset(p.get("stageSha256", {}))
                        for p in parts)):
                issues.append({"id": key, "code": "incomplete_stage_evidence", "report": label})
            for stage in ("converter", "independent"):
                state = row.get(stage, {})
                if not state or (state.get("status") == "rendered" and not state.get("performanceSha256")):
                    issues.append({"id": key, "code": "missing_performance_evidence", "stage": stage, "report": label})
        for stage in ("converter", "independent", "reference", "preparationStatus",
                      "preparationDifferences", "approvedPreparationDifferences",
                      "authoredEventStatus", "authoredEventDifferences"):
            if a.get(stage) != b.get(stage):
                changes.append({"id": key, "stage": stage,
                                "beforeSha256": fingerprint(a.get(stage)),
                                "afterSha256": fingerprint(b.get(stage))})
    if before.get("sourceUnavailable") or after.get("sourceUnavailable"):
        issues.append({"code": "unavailable_sources"})
    return {"version": 1, "scope": "Recorded development stages only; not import acceptance.",
            "status": "incomplete" if issues else "changed" if changes else "unchanged",
            "before": {k: before.get(k) for k in ("implementation", "referenceIdentity")},
            "after": {k: after.get(k) for k in ("implementation", "referenceIdentity")},
            "issues": issues, "changes": changes, "comparedCases": len(left.keys() & right.keys())}
