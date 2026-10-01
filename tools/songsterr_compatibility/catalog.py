"""Capability metadata and guard inventory, not shared musical calculations."""
import ast
from pathlib import Path
from feedback_converter.song_import.compatibility import KNOWN, UNIMPLEMENTED, LIMITATIONS, VERSION

ROOT = Path(__file__).resolve().parents[2]
RULES = {
    "timing.basic": {"files": ["songsterr.py", "model.py"], "policy": "source_interpretation"},
    "timing.strum_grace": {"files": ["songsterr_timing.py"], "policy": "source_interpretation"},
    "timing.grace": {"files": ["songsterr_timing.py"], "policy": "source_interpretation"},
    "timing.swing": {"files": ["songsterr_timing.py"], "policy": "source_interpretation"},
    "timing.repeats": {"files": ["repeat_regions.py", "timeline.py"], "policy": "source_interpretation"},
    "timing.ties": {"files": ["timeline.py", "tie_continuity.py"], "policy": "source_interpretation"},
    "timing.tempo": {"files": ["songsterr_automation.py"], "policy": "source_interpretation"},
    "timing.pickup": {"files": ["songsterr_pickup.py"], "policy": "source_interpretation"},
    "notation.voices": {"files": ["voices.py"], "policy": "approved_game_projection"},
    "technique.harmonics": {"files": ["songsterr_harmonics.py", "tied_harmonics.py"], "policy": "approved_game_projection"},
    "technique.trills": {"files": ["songsterr_trills.py"], "policy": "approved_game_projection"},
    "technique.whammy": {"files": ["songsterr_whammy.py"], "policy": "approved_game_projection"},
    "projection.high_frets": {"files": ["high_frets.py"], "policy": "approved_omission"},
    "projection.hybrid": {"files": ["hybrid.py"], "policy": "derived_arrangement"},
}
DEFERRED = {
    "strum_grace_consumed_attack": "Omitting an attack consumed by strum/grace timing requires a decision.",
    "source_player_repairs": "Source-player fret repairs and source-relationship repairs are not automatically authorized.",
    "source_player_synthesis": "Humanization, sample envelopes and automatic strumming are not authored gameplay.",
}


def inventory():
    """Every explicit guard is owned or visibly unclassified, never silently absent."""
    rows = []
    for path in sorted((ROOT / "src/feedback_converter/song_import").glob("*.py")):
        for n in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
            if isinstance(n, ast.Raise):
                owners = [k for k, v in RULES.items() if path.name in v["files"]]
                rows.append({"file": path.name, "line": n.lineno,
                             "guard": ast.unparse(n.exc) if n.exc else "rethrow",
                             "candidateRules": owners, "classification": "file_family" if owners else "unclassified"})
    fields = [{"field": f"{scope}.{key}", "declarationVersion": VERSION,
               "status": "unimplemented" if key in UNIMPLEMENTED.get(scope, ()) else "recognized_conditional",
               "limitation": LIMITATIONS.get((scope, key)),
               "owner": "compatibility.py", "ruleMapping": "unmapped"}
              for scope, keys in sorted(KNOWN.items()) for key in sorted(keys)]
    return {"version": 1, "rules": RULES, "deferred": DEFERRED, "guards": rows, "fields": fields,
            "unclassifiedGuards": sum(not r["candidateRules"] for r in rows),
            "scope": "File-family ownership is a review aid, not proof that a guard implements a rule."}
