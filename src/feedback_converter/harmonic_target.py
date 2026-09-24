"""Additive, fretted-harmonic FeedPak contract (natural hn/hps stay separate)."""
import math

NODES = {12: 12, 7: 19, 19: 19, 5: 24, 24: 24, 4: 28, 9: 28, 16: 28,
         3.2: 31, 2.7: 34, 5.8: 34, 9.6: 34, 14.7: 34, 21.7: 34,
         2.4: 36, 8.2: 36, 17: 36}
POLICIES = {"pinch": "harmonic", "artificial": "harmonic", "tapped": "harmonic",
            "semi": "mixed", "feedback": "attack_either"}
ALIAS = "songsterr-natural-15"


def target_for(kind, node):
    if (not isinstance(kind, str) or kind not in POLICIES
            or type(node) not in (int, float) or not math.isfinite(node)):
        return None
    key = next((n for n in NODES if abs(n - node) <= 1e-9), None)
    return None if key is None else {"kind": kind, "node": key,
                                    "interval": NODES[key], "policy": POLICIES[kind]}


def valid_target(value):
    if not isinstance(value, dict) or set(value) != {"kind", "node", "interval", "policy"}:
        return False
    expected = target_for(value["kind"], value["node"])
    return (expected is not None and type(value["interval"]) is int
            and value == expected and type(value["node"]) in (int, float))


def valid_note_target(note):
    """Validate the whole note, including flags that control legacy rendering."""
    target = note.get("harmonic_target")
    if not valid_target(target) or type(note.get("f")) is not int or not 0 <= note["f"] <= 48:
        return False
    if note.get("mt") or note.get("fhm") or note.get("hm") or "hn" in note or "hps" in note or "harmonic_alias" in note:
        return False
    return (note.get("hp") is True) == (target["kind"] in ("pinch", "semi"))
