"""Account for source fields without guessing what unfamiliar music means."""
from copy import deepcopy

from .model import ScoreImportError
from .songsterr_legacy import validate_legacy_field
from .songsterr_legacy_effects import validate_legacy_beat_effect
from .songsterr_fields import validate_octave_clef


def _retained_part_index(value):
    """Optional source numbering never selects, sorts, or identifies parts."""
    return value is None or (type(value) in (int, float)
                             and 0 <= value <= 9_007_199_254_740_991
                             and value == int(value))


def _matching_voice_count(part, value):
    """A redundant count never selects or limits the actual musical voices."""
    if type(value) is not int or value <= 0:
        return False
    measures = part.get("measures")
    if not isinstance(measures, list) or not measures:
        return False
    maximum = 0
    for measure in measures:
        if not isinstance(measure, dict):
            return False
        voices = measure.get("voices")
        if not isinstance(voices, list) or not voices or not all(isinstance(v, dict) for v in voices):
            return False
        maximum = max(maximum, len(voices))
    return value == maximum


class FeatureInventory:
    def __init__(self):
        self._entries = {}

    def record(self, scope, field, handling, path, representations=None):
        key = (scope, field, handling)
        entry = self._entries.setdefault(key, {"scope": scope, "field": field,
                                              "handling": handling, "count": 0, "examples": [],
                                              "representations": ["source"]})
        defaults = [handling] if handling in {"playable", "notation", "layout"} else []
        entry["representations"] = sorted(set(entry["representations"]) | set(representations or defaults))
        entry["count"] += 1
        if len(entry["examples"]) < 3:
            entry["examples"].append(path)

    def inspect(self, obj, scope, path, *, playable=(), notation=(), retained=(), layout=(), strict=False):
        if not isinstance(obj, dict):
            raise ScoreImportError(f"Malformed {scope} object at {path}.")
        for key, value in obj.items():
            where = f"{path}.{key}"
            if scope == "Songsterr beat" and key == "tremoloBar" and type(value) is bool:
                self.record(scope, key, "source", where, ["source"])
                continue
            if scope == "Songsterr beat" and key == "octaveClef":
                validate_octave_clef(value)
                self.record(scope, key, "source", where, ["source"])
                continue
            # The part-level opaque string is metadata, not a key signature.
            # Check before inactive handling so malformed values stay visible.
            if scope == "Songsterr part" and key == "key" and isinstance(value, str):
                self.record(scope, key, "source", where)
                continue
            if scope == "Songsterr part" and key == "voices" and _matching_voice_count(obj, value):
                self.record(scope, key, "source", where)
                continue
            if scope == "Songsterr part" and key == "index" and _retained_part_index(value):
                self.record(scope, key, "source", where)
                continue
            if validate_legacy_beat_effect(scope, key, value, beat=obj):
                self.record(scope, key, 'source', where, ['source'])
                continue
            # Metadata must be validated before generic inactive handling,
            # including note fields on rests that never reach the note parser.
            if scope.startswith("Songsterr ") and validate_legacy_field(scope, key, value):
                handling = "source"
                self.record(scope, key, handling, where, ["source"])
                continue
            if key in playable:
                handling = "playable"
            elif key in notation:
                handling = "notation"
            elif key in retained:
                handling = "source"
            elif key in layout:
                handling = "layout"
            elif (value is None or value is False or value == "" or value == [] or value == {}) and not (
                    scope == "Songsterr part" and key in {"key", "voices", "index"}):
                handling = "inactive_unknown"
            else:
                handling = "unclassified"
                self.record(scope, key, handling, where)
                if strict:
                    raise ScoreImportError(f"Unsupported {scope} field at {where}; its musical meaning is not implemented.")
                continue
            representations = ["source"]
            if key in playable:
                representations.append("playable")
            if key in notation:
                representations.append("notation")
            if key in layout:
                representations.append("layout")
            self.record(scope, key, handling, where, representations)

    def entries(self):
        return [deepcopy(entry) for _, entry in sorted(self._entries.items())]

    def warnings(self):
        return [f"Unclassified {entry['scope']} field {entry['field']} is retained in source evidence only."
                for entry in self._entries.values() if entry["handling"] == "unclassified"]
