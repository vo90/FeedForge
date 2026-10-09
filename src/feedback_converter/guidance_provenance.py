"""FeedPak guidance ownership v1. Stdlib-only; shared verbatim with producers.

Integrity is not authorship authentication. Unknown data is never promoted.
Run on raw JSON at load/save boundaries, never in the rendering loop.
"""
from copy import deepcopy
import hashlib
import json

KEY = "guidanceProvenance"
FIELDS = ("anchors", "handshapes", "chords", "templates")
MUSIC = ("tuning", "capo", "centOffset", "notes", "chords", "templates")
ORIGINS = ("source", "user", "generated", "unknown")


def _numbers(value):
    # JSON 2 and 2.0 have the same chart meaning. Do not round timestamps.
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {k: _numbers(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_numbers(v) for v in value]
    return value


def digest(value):
    return hashlib.sha256(json.dumps(_numbers(value), sort_keys=True,
        ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def input_digest(chart, field=None):
    values = {k: chart[k] for k in MUSIC if k in chart}
    if field in (None, "anchors"):
        values["beats"] = chart.get("beats", [])
    return digest(values)


def record(chart, field):
    ext = chart.get("ext")
    proof = ext.get(KEY) if isinstance(ext, dict) else None
    if not isinstance(proof, dict) or type(proof.get("version")) is not int or proof.get("version") != 1:
        return None
    fields = proof.get("fields")
    value = fields.get(field) if isinstance(fields, dict) else None
    return value if isinstance(value, dict) else None


def resolve(chart, field):
    """Return independent origin, integrity and applicability; never throw."""
    result = {"origin": "unknown", "integrity": "unknown", "applicability": "unknown"}
    try:
        proof = record(chart, field)
        if not proof:
            return _legacy_resolution(chart, field)
        origin = proof.get("origin")
        if origin not in (*ORIGINS, "mixed") or not isinstance(proof.get("producer"), str) or not proof["producer"]:
            return result
        result["origin"] = origin
        if field not in chart or proof.get("sha256") != digest(chart[field]):
            result["integrity"] = "modified"
            return result
        rows = proof.get("rows")
        if origin == "mixed":
            if (not isinstance(rows, list) or len(rows) != len(chart[field])
                    or any(not isinstance(r, dict) or r.get("origin") not in ORIGINS
                           or r.get("sha256") != digest(v) for r, v in zip(rows, chart[field]))):
                return result
        result["integrity"] = "valid"
        if origin == "generated" or origin == "mixed":
            result["applicability"] = "current" if proof.get("inputSha256") == input_digest(chart, field) else "stale"
        else:
            result["applicability"] = "supplied"
        return result
    except (ValueError, TypeError, OverflowError):
        return result


def _legacy_resolution(chart, field):
    result = {"origin": "unknown", "integrity": "unknown", "applicability": "unknown"}
    ext = chart.get("ext", {})
    if not isinstance(ext, dict) or KEY in ext:
        return result
    proof = ext.get("chartGuidance")
    if not isinstance(proof, dict) or proof.get("policy") != "feedforge-chart-guidance-v2" or proof.get("sourceAuthored") is not False:
        return result
    fields = proof.get("fields")
    policies = (None, "chord-local-v1", "open-preparation-v1", "open-preparation-v2", "slide-follow-v1", "positionless-preparation-v1")
    if (not isinstance(fields, list) or field not in fields or len(set(fields)) != len(fields)
            or any(k not in ("anchors", "handshapes") for k in fields)
            or proof.get("positionPolicy") not in policies):
        return result
    def raw_digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
            allow_nan=False, separators=(",", ":")).encode()).hexdigest()
    result["origin"] = "generated"
    if proof.get("guidanceSha256") != raw_digest({k: chart.get(k) for k in fields}):
        result["integrity"] = "modified"
        return result
    result["integrity"] = "valid"
    result["applicability"] = "current" if proof.get("musicSha256") == raw_digest(
        {k: chart[k] for k in MUSIC if k in chart}) else "stale"
    return result


def origins(chart, field):
    state = resolve(chart, field)
    values = chart.get(field, [])
    if state["integrity"] != "valid":
        return ["unknown"] * len(values)
    proof = record(chart, field)
    if state["origin"] == "mixed":
        return [r["origin"] for r in proof["rows"]]
    return [state["origin"]] * len(values)


def stamp(chart, field, origin, *, producer, policy=None, row_origins=None,
          intentional_empty=False, parent=None):
    """Declare only data this producer actually supplied or transformed."""
    if field not in FIELDS or origin not in (*ORIGINS, "mixed"):
        raise ValueError("Unsupported guidance ownership")
    ext = chart.setdefault("ext", {})
    existing = ext.get(KEY)
    if existing is not None and (not isinstance(existing, dict) or type(existing.get("version")) is not int
                                or existing.get("version") != 1 or not isinstance(existing.get("fields"), dict)):
        raise ValueError("Unsupported guidance provenance; preserve for review")
    values = chart[field]
    proof = {"origin": origin, "producer": producer, "sha256": digest(values)}
    if row_origins is not None:
        if len(row_origins) != len(values) or any(o not in ORIGINS for o in row_origins):
            raise ValueError("Invalid guidance row ownership")
        unique = set(row_origins)
        proof["origin"] = next(iter(unique)) if len(unique) == 1 else origin
        if len(unique) > 1:
            proof["origin"] = "mixed"
            proof["rows"] = [{"sha256": digest(v), "origin": o} for v, o in zip(values, row_origins)]
    if proof["origin"] in ("generated", "mixed"):
        proof["inputSha256"] = input_digest(chart, field)
        proof["policy"] = policy or "preserved"
    if intentional_empty:
        if values:
            raise ValueError("Intentionally empty guidance must be empty")
        proof["intentionalEmpty"] = True
    if parent:
        proof["parentSha256"] = parent
    ext.setdefault(KEY, {"version": 1, "fields": {}}).setdefault("fields", {})[field] = proof
    return proof


def protected(chart, field):
    """Any explicit ownership prevents missing-field inference, even if stale."""
    ext = chart.get("ext", {})
    if not isinstance(ext, dict):
        return False
    # A future contract cannot safely be interpreted by an older generator.
    if KEY in ext and (not isinstance(ext[KEY], dict) or ext[KEY].get("version") != 1):
        return True
    proof = ext.get(KEY)
    if isinstance(proof, dict):
        fields = proof.get("fields")
        return not isinstance(fields, dict) or field in fields
    return False


def carry(chart, target, field, *, producer, row_origins=None):
    """Record a known copy/clip/retime; callers must provide row associations."""
    state = resolve(chart, field)
    if state["integrity"] != "valid":
        return
    proof = record(chart, field) or {"sha256": digest(chart[field]),
        "policy": chart.get("ext", {}).get("chartGuidance", {}).get("positionPolicy")}
    if row_origins is None and target[field] != chart[field]:
        # Uniform source/user collections can be clipped without ambiguity.
        if state["origin"] not in ("source", "user"):
            return
    copied = stamp(target, field, state["origin"], producer=producer,
          policy=proof.get("policy"), row_origins=row_origins,
          intentional_empty=bool(proof.get("intentionalEmpty") and not target[field]),
          parent=proof["sha256"])
    if state["applicability"] == "stale":
        copied["inputSha256"] = proof.get("inputSha256")


def edited(before, after, *, producer="feedback-editor-v1", generated_anchors=False):
    """Preserve untouched receipts; user edits own only changed rows.

    Caller compares wire data, not UI display labels. Equal rows are matched
    by digest, so inserts/deletes cannot move ownership to a different row.
    """
    after["ext"] = deepcopy(before.get("ext", {}))
    for field in FIELDS:
        if field not in after:
            continue
        if field == "anchors" and generated_anchors:
            stamp(after, field, "generated", producer=producer, policy="editor-auto-anchors-v1")
            continue
        if after[field] == before.get(field):
            continue
        old = {}
        for value, origin in zip(before.get(field, []), origins(before, field)):
            key = digest(value)
            # Ambiguous identical rows conservatively lose authority.
            old[key] = origin if key not in old or old[key] == origin else "unknown"
        row_origins = [old.get(digest(v), "user") for v in after[field]]
        stamp(after, field, "user", producer=producer, row_origins=row_origins,
              intentional_empty=not after[field])
        previous = record(before, field)
        current = record(after, field)
        if previous and "generated" in row_origins:
            # Preserving a generated row does not refresh its input evidence.
            current["inputSha256"] = previous.get("inputSha256")
            current["policy"] = previous.get("policy", "preserved")
    return after
