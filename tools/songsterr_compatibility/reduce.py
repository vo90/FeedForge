"""Bounded automatic reduction of reproducible, qualified stage differences."""
from copy import deepcopy
from .audit import minimize, run, canonical_hash


def signatures(result):
    return {(stage, d["code"], d.get("part"))
            for stage in ("preparation", "authoredEvent")
            if result.get(stage + "Status") == "different"
            for d in result.get(stage + "Differences", [])
            if "not_tested" not in d["code"] and d["code"] != "reference_error"}


def reduce_case(case, *, worker=None, node="node", limit=30, runner=None):
    """Every candidate must reproduce on both sides; never reduce parser errors.

    The source is copied, navigation/automation guards remain in `minimize`,
    and the retained result is rechecked. This is a bounded reduction, not a
    claim to find the globally smallest example for context-sensitive music.
    """
    if not 1 <= limit <= 100:
        raise ValueError("Reduction limit must be between 1 and 100")
    if runner is None:
        if worker is None:
            raise ValueError("Reduction requires a reviewed reference")
        runner = lambda source: run([{**case, "source": source}], worker=worker, node=node)["cases"][0]
    original = deepcopy(case["source"])
    baseline = runner(deepcopy(original))
    target = signatures(baseline)
    if not target:
        return {"status": "not_reduced", "reason": "No qualified musical stage difference",
                "sourceSha256": canonical_hash(original), "attempts": 0}
    attempts = 0

    def reproduces(candidate):
        nonlocal attempts
        attempts += 1
        result = runner(deepcopy(candidate))
        # A parse/adapter failure is not a smaller musical mismatch.
        return (result.get("preparationStatus") != "not_tested"
                and bool(target & signatures(result)))

    reduced = minimize(original, reproduces, limit=limit)
    checked = runner(deepcopy(reduced))
    if not target & signatures(checked):
        raise ValueError("Reduced case did not reproduce on final recheck")
    return {"status": "reduced" if reduced != original else "context_retained",
            "originalSha256": canonical_hash(original), "reducedSha256": canonical_hash(reduced),
            "attempts": attempts, "source": reduced,
            "differences": {k: checked.get(k) for k in ("preparationDifferences", "authoredEventDifferences")},
            "scope": "Qualified development difference only; original source is unchanged."}
