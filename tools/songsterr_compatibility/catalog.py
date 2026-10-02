"""Capability metadata and guard inventory, not shared musical calculations."""
import ast
from copy import deepcopy
import json
from pathlib import Path
from feedback_converter.song_import.compatibility import KNOWN, UNIMPLEMENTED, LIMITATIONS, VERSION
from .cases import cases

ROOT = Path(__file__).resolve().parents[2]
RULES = {
    "timing.basic": {"files": ["songsterr.py", "model.py"], "policy": "source_interpretation"},
    "timing.strum_grace": {"files": ["songsterr_timing.py"], "policy": "source_interpretation"},
    "timing.grace": {"files": ["songsterr_timing.py"], "policy": "source_interpretation"},
    "timing.swing": {"files": ["songsterr_timing.py"], "policy": "source_interpretation"},
    "timing.repeats": {"files": ["repeat_regions.py", "timeline.py"], "policy": "source_interpretation"},
    "timing.ties": {"files": ["timeline.py", "muted_ties.py", "tied_harmonics.py"], "policy": "source_interpretation"},
    "timing.tempo": {"files": ["songsterr_automation.py"], "policy": "source_interpretation"},
    "timing.pickup": {"files": ["songsterr_pickup.py"], "policy": "source_interpretation"},
    "timing.whole_rest": {"files": ["songsterr_fields.py", "songsterr_timing.py"], "policy": "silent_measure_boundary"},
    "notation.voices": {"files": ["voices.py"], "policy": "approved_game_projection"},
    "notation.picking_hand": {"files": ["fingering.py"], "policy": "retained_annotation"},
    "expression.sustain_pedal": {"files": ["songsterr_fields.py"], "policy": "retained_synth_expression"},
    "technique.harmonics": {"files": ["songsterr_harmonics.py", "tied_harmonics.py"], "policy": "approved_game_projection"},
    "technique.trills": {"files": ["songsterr_trills.py", "tied_trills.py"], "policy": "approved_game_projection"},
    "technique.tremolo_picking": {"files": ["songsterr_tremolo.py"], "policy": "existing_game_instruction"},
    "technique.whammy": {"files": ["songsterr_whammy.py"], "policy": "approved_game_projection"},
    "projection.high_frets": {"files": ["high_frets.py"], "policy": "approved_omission"},
    "projection.hybrid": {"files": ["hybrid_lead.py", "hybrid_selection.py", "hybrid_materialize.py"], "policy": "derived_arrangement"},
}
# Explicit ownership of the first timing scope and established technique models.
# This metadata does not share producer/verifier calculations or grant support.
FIELDS = {
    "timing.basic": "measure.signature beat.duration beat.type beat.dots beat.tuplet note.string note.fret",
    "timing.strum_grace": "beat.arpeggio beat.brushStroke beat.upArpeggio beat.downArpeggio",
    "timing.grace": "beat.graceNote",
    "timing.swing": "measure.tripletFeel",
    "timing.repeats": "measure.repeat measure.repeatStart measure.alternateEnding",
    "timing.ties": "note.tie",
    "timing.tempo": "automations.tempo automations.fermata automations.gradualTempo tempo.measure tempo.position tempo.bpm tempo.type tempo.dotted tempo.linear",
    "timing.whole_rest": "beat.rest beat.type beat.dots beat.duration note.rest",
    "notation.voices": "measure.voices voice.beats",
    "notation.picking_hand": "note.rightFingering",
    "expression.sustain_pedal": "beat.sustainPedal",
    "technique.harmonics": "note.harmonic note.harmonicFret",
    "technique.trills": "note.trill",
    "technique.tremolo_picking": "beat.tremolo note.tremolo",
    "technique.whammy": "beat.tremoloBar beat.vibratoWithTremoloBar",
    "projection.high_frets": "note.fret",
}
FAMILIES = {
    "timing.basic": {"timing.basic"},
    "timing.strum_grace": {"timing.strum_grace", "timing.strum_tie_grace", "timing.strum_legacy"},
    "timing.grace": {"timing.grace", "timing.strum_grace", "timing.strum_tie_grace"},
    "timing.swing": {"timing.swing", "timing.swing_tuplet_repeat"},
    "timing.repeats": {"timing.repeats", "timing.swing_tuplet_repeat"},
    "timing.ties": {"timing.ties", "timing.strum_tie_grace"},
}
BEHAVIOR = {
    "timing.tempo": "Validate every entry; use the last complete instruction at an exact coordinate before hold/ramp expansion. Retain and report earlier entries and qualified inactive marks beyond the score. Missing initial clocks, outside marks with active ramps, and cross-track clock conflicts remain blocking.",
    "expression.sustain_pedal": "Retain validated pedal flags and disclose absent synthesis/engraving/scoring support. Preserve written/tied note timing; do not turn MIDI pedal control into longer game trails.",
    "notation.picking_hand": "Validate P/I/M/A/C; retain the picking-hand annotation and disclose absent engraving without changing fret-hand hints, pitches, attacks or scoring.",
    "timing.basic": "Preserve exact authored fractions until mapping the performed clock.",
    "technique.tremolo_picking": "Map active beat/per-string tremolo to tr; retain exact subdivision and within-tie timing in source and disclose the whole-sustain display/rate limitation. No expanded scored attacks.",
    "timing.strum_grace": "Apply explicit stroke timing after grace allocation. Omit only non-sounding attacks on grace-shortened beats under the approved consumed-strum policy, preserving the source and independently verified per-occurrence receipts. Tied or linked gestures remain protected.",
    "timing.grace": "Allocate source grace groups with bar and opening context; do not invent minimum note lengths.",
    "timing.swing": "Apply the authored rhythmic feel to eligible groups while retaining written rhythm.",
    "timing.repeats": "Expand authored traversal and preserve source identity plus occurrence.",
    "timing.ties": "Join supported continuations without a new attack. At a repeat jump, retain only an exact-boundary continuation into an explicit same-voice/string/fret entrance tie. Pitch/relationship repairs are not inferred from synthesis.",
    "timing.whole_rest": "Bound a qualifying silent whole-rest overrun to the meter; never truncate played notes by this rule.",
}
DEFERRED = {
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
    rules = deepcopy(RULES)
    generated = list(cases())
    reference_hash = json.loads(Path(__file__).with_name('reference-manifest.json').read_text())["sha256"]
    for key, value in rules.items():
        value["sourceFields"] = FIELDS.get(key, "").split()
        value["behavior"] = BEHAVIOR.get(key, "See the existing implementation and policy tests; external effect comparison is not qualified.")
        value["testCases"] = [c['id'] for c in generated if c['family'] in FAMILIES.get(key, set())]
        value["reference"] = {"assetSha256": reference_hash,
                              "status": "qualified_examples" if value["testCases"] else "not_qualified",
                              "scope": "Preparation and authored pre-tie events only; not complete effect or game equivalence."}
        value["prerequisites"] = ["approved_revision", "valid_source_structure", "source_identity_preserved"]
        value["interactions"] = [other for other in FAMILIES if other != key and
                                  FAMILIES.get(key, set()) & FAMILIES[other]]
        if key == 'timing.strum_grace':
            value['reference']['scope'] += ' Consumed attacks additionally checked against captured explicit-source and default-player playback suppression.'
            value['testFixture'] = 'tests/fixtures/songsterr_consumed_strum_playback.json'
            value['tests'] = 'tests/test_songsterr_consumed_strums.py'
        if key == 'timing.ties':
            value['reference']['scope'] += ' Exact-boundary repeat entrance ties additionally checked against native ks merging; no pitch/gap repair qualification.'
            value['testFixture'] = 'tests/fixtures/songsterr_repeat_tie_reference.json'
            value['tests'] = 'tests/test_songsterr_repeat_ties.py'
        if key == 'timing.tempo':
            value['reference']['scope'] += ' Exact-coordinate instruction precedence additionally checked before holds/ramps and against emitted tempo events.'
            value['reference']['scope'] += ' Outside-score steps qualified with an explicit initial clock and no active ramps; native preparation, emitted tempo and note scheduling match without them.'
            value['testFixture'] = 'tests/fixtures/songsterr_tempo_precedence_reference.json'
            value['tests'] = 'tests/test_songsterr_tempo_precedence.py'
            value['additionalFixtures'] = ['tests/fixtures/songsterr_inactive_tempo_reference.json']
            value['additionalTests'] = ['tests/test_songsterr_inactive_tempos.py']
        if key == 'timing.whole_rest':
            value['reference']['status'] = 'qualified_examples'
            value['testFixture'] = 'tests/fixtures/songsterr_rest_reference.json'
            value['tests'] = 'tests/test_songsterr_dotted_whole_rest.py'
        if key == 'technique.tremolo_picking':
            value['reference']['status'] = 'qualified_examples'
            value['reference']['scope'] = 'Beat/per-note scope and precedence only; rate remains source evidence, not expanded gameplay.'
            value['testFixture'] = 'tests/fixtures/songsterr_tremolo_reference.json'
            value['tests'] = 'tests/test_songsterr_note_tremolo.py'
        if key == 'notation.picking_hand':
            value['reference']['status'] = 'qualified_examples'
            value['reference']['scope'] = 'Pinned source enum and unchanged sampled chord/tie/repeat scheduling; picking-hand engraving is not represented.'
            value['testFixture'] = 'tests/fixtures/songsterr_right_fingering_reference.json'
            value['tests'] = 'tests/test_songsterr_right_fingering.py'
        if key == 'expression.sustain_pedal':
            value['reference']['status'] = 'qualified_examples'
            value['reference']['scope'] = 'Pinned scheduler emits MIDI CC64 on/off across merged pedal spans; sampled note attacks/releases unchanged. Synthesized sounding duration is not claimed equivalent.'
            value['testFixture'] = 'tests/fixtures/songsterr_sustain_pedal_reference.json'
            value['tests'] = 'tests/test_songsterr_sustain_pedal.py'
    fields = [{"field": f"{scope}.{key}", "declarationVersion": VERSION,
               "status": "unimplemented" if key in UNIMPLEMENTED.get(scope, ()) else "recognized_conditional",
               "limitation": LIMITATIONS.get((scope, key)),
               "owner": "compatibility.py",
               "rules": [r for r, v in rules.items() if f"{scope}.{key}" in v["sourceFields"]],
               "ruleMapping": "mapped" if any(f"{scope}.{key}" in v["sourceFields"] for v in rules.values()) else "unmapped"}
              for scope, keys in sorted(KNOWN.items()) for key in sorted(keys)]
    return {"version": 2, "rules": rules, "deferred": DEFERRED, "guards": rows, "fields": fields,
            "unclassifiedGuards": sum(not r["candidateRules"] for r in rows),
            "scope": "File-family ownership is a review aid, not proof that a guard implements a rule."}
