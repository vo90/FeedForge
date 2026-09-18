"""Conservative additive inspection evidence, separate from playable chart data."""
from __future__ import annotations

import re
from typing import Any


def minimum_used_strings(chart: Any) -> int | None:
    """A lower bound from notes, never an exact instrument string count."""
    if not isinstance(chart, dict):
        return None
    used: set[int] = set()
    notes = list(chart.get("notes") or []) if isinstance(chart.get("notes"), list) else []
    for chord in chart.get("chords") if isinstance(chart.get("chords"), list) else []:
        if isinstance(chord, dict) and isinstance(chord.get("notes"), list):
            notes.extend(chord["notes"])
    for note in notes:
        value = note.get("s") if isinstance(note, dict) else None
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 7:
            used.add(value)
    return max(used) + 1 if used else None


def psarc_instrument_evidence(source_path: str, metadata: dict[str, Any], chart: Any) -> dict[str, Any]:
    # Arrangement names here come from matched manifest path flags/name fields.
    # An arbitrary SNG filename is not authoritative instrument-family evidence.
    normalized = source_path.replace("\\", "/").lower()
    source_stem = normalized.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    families: set[str] = set()
    for key, name in (metadata.get("arrangement_names") or {}).items():
        match_key = str(key).replace("\\", "/").lower()
        key_stem = match_key.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        if key and (match_key == normalized or key_stem == source_stem):
            value = str(name).lower().strip()
            if re.fullmatch(r"bass(?:[\s_-]*\d+)?", value):
                families.add("bass")
            elif re.fullmatch(r"(?:lead|rhythm|guitar)(?:[\s_-]*\d+)?", value):
                families.add("guitar")
    family = next(iter(families)) if len(families) == 1 else None
    return {
        "instrument_family": family,
        "instrument_family_evidence": "explicit" if family else "unknown",
        # Rocksmith's parsed SNG metadata has tuning slots but no exact count.
        "string_count": None,
        "string_count_evidence": "unknown",
        "minimum_used_strings": minimum_used_strings(chart),
    }


def feedpak_instrument_evidence(entry: dict[str, Any], chart: Any) -> dict[str, Any]:
    families = {value for value in (entry.get("instrument_family"), entry.get("type")) if value in ("guitar", "bass")}
    family = next(iter(families)) if len(families) == 1 else None
    if entry.get("instrument_family_evidence", "explicit") != "explicit":
        family = None
    count = entry.get("string_count")
    minimum = minimum_used_strings(chart)
    explicit_count = (isinstance(count, int) and not isinstance(count, bool) and 4 <= count <= 8
                      and entry.get("string_count_evidence", "explicit") == "explicit"
                      and (minimum is None or minimum <= count))
    # The explicit field is evidence. Tuning-array length and note usage are not.
    return {
        "instrument_family": family,
        "instrument_family_evidence": "explicit" if family else "unknown",
        "string_count": count if explicit_count else None,
        "string_count_evidence": "explicit" if explicit_count else "unknown",
        "minimum_used_strings": minimum,
    }


def backing_evidence(manifest: dict[str, Any]) -> dict[str, Any]:
    value = manifest.get("backing_track")
    explicit = value in ("full", "no-guitar", "no-bass") and manifest.get("backing_track_evidence") == "explicit"
    # A converter-generated stem named 'full' merely means the copied mix. It
    # cannot establish whether a CDLC author removed guitar/bass from that mix.
    return {"backing_track": value if explicit else None, "backing_track_evidence": "explicit" if explicit else "unknown"}
