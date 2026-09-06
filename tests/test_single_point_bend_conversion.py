"""One authored bend target must survive conversion and consumer sampling."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from types import SimpleNamespace as NS

import pytest

from feedback_converter import converter as c


def _source(time=167.598999, target_time=167.824005, value=2.0, sustain=0.394):
    return NS(time=time, sustain=sustain, string=3, fret=9,
        mask=c.NOTE_MASK_PINCHHARMONIC, chordId=c.UINT32_NONE,
        slideTo=-1, slideUnpitchTo=-1, bend_time=value, leftHand=-1,
        bends=[NS(time=target_time, step=value)])


def test_single_future_target_keeps_authored_time_without_an_invented_release():
    note = c._note_to_feedpak(_source())
    assert note["bnv"] == [{"t": 0.0, "v": 0.0}, {"t": 0.225006, "v": 2.0}]
    assert note["sus"] == 0.394 and note["bn"] == 2.0 and note["hp"] is True


def test_single_onset_target_remains_a_held_prebend():
    note = c._note_to_feedpak(_source(time=10.0, target_time=10.0))
    assert note["bnv"] == [{"t": 0.0, "v": 2.0}]


def test_single_pre_onset_target_is_held_and_reported_as_exceptional():
    adjustments = set()
    note = c._note_to_feedpak(_source(time=10.0, target_time=9.98), bend_adjustments=adjustments)
    assert note["bnv"] == [{"t": 0.0, "v": 2.0}]
    assert adjustments == {"entirely-pre-onset"}


def test_single_target_beyond_sustain_samples_target_ramp_at_note_end():
    note = c._note_to_feedpak(_source(time=10.0, target_time=11.0, sustain=0.5))
    assert note["bnv"] == [{"t": 0.0, "v": 0.0}, {"t": 0.5, "v": 1.0}]
    assert note["bn"] == 1.0


def test_scalar_without_authored_points_stays_scalar():
    source = _source()
    source.bends = []
    assert c._note_to_feedpak(source)["bn"] == 2.0
    assert "bnv" not in c._note_to_feedpak(source)


def test_lone_zero_target_keeps_source_facts_without_inventing_missing_initial_shape():
    # Accept 227.904: declared bend=1, sole point at 228.261 with step=0.
    source = _source(time=227.904, target_time=228.261, value=0, sustain=0.6)
    source.bend_time = 1.0
    adjustments = set()
    note = c._note_to_feedpak(source, bend_adjustments=adjustments)
    assert note["bn"] == 1.0
    assert note["bnv"] == [{"t": 0.357, "v": 0.0}]
    assert adjustments == {"zero-target"}


def test_single_target_survives_flat_and_every_source_difficulty_copy():
    source = _source()
    song = NS(metadata=NS(tuning=[0] * 6, capo=0), chordTemplates=[], chordNotes=[],
        levels=[NS(difficulty=d, notes=[source], anchors=[], fingerprints=[[], []]) for d in [0, 1]],
        phraseIterations=[NS(phraseId=0, time=160.0, endTime=170.0)],
        phrases=[NS(name="solo", maxDifficulty=1)], beats=[], sections=[])
    chart = c._song_to_arrangement(song, "caught-up_lead.sng", {}, include_tones=False)
    expected = [{"t": 0.0, "v": 0.0}, {"t": 0.225006, "v": 2.0}]
    assert chart["notes"][0]["bnv"] == expected
    assert all(level["notes"][0]["bnv"] == expected for level in chart["phrases"][0]["levels"])


def test_single_target_inside_chord_retains_its_time_and_string_sustain():
    source = _source()
    chord_note = NS(mask=[0, 0, 0, c.NOTE_MASK_SUSTAIN, 0, 0],
        bends=[NS(bendValues=source.bends if s == 3 else [], count=1 if s == 3 else 0) for s in range(6)],
        slideTo=[-1] * 6, slideUnpitchTo=[-1] * 6)
    song = NS(chordTemplates=[NS(frets=[-1, -1, -1, 9, -1, -1])], chordNotes=[chord_note])
    chord = NS(time=source.time, sustain=source.sustain, chordNoteId=0, mask=0)
    assert c._chord_notes(song, chord, 0)[0]["bnv"] == c._note_to_feedpak(source)["bnv"]


def test_actual_feedback_renderer_interpolates_generated_target_then_holds():
    script_path = os.environ.get("FEEDBACK_HIGHWAY_SCRIPT")
    if not script_path:
        pytest.skip("set FEEDBACK_HIGHWAY_SCRIPT to the tested 3D screen.js for consumer validation")
    node = shutil.which("node")
    if not node:
        pytest.fail("Node.js is required for the explicitly requested renderer contract check")
    source = Path(script_path).read_text(encoding="utf-8")
    match = re.search(r"function bnvSampleAt\(bnv, t\) \{[\s\S]*?\n        \}", source)
    assert match, "could not locate the actual renderer bend sampler"
    curve = c._note_to_feedpak(_source())["bnv"]
    code = match.group(0) + "\nconst curve=" + json.dumps(curve) + ";\n"
    code += "console.log(JSON.stringify([0,0.112503,0.225006,0.394].map(t=>bnvSampleAt(curve,t))));"
    result = subprocess.run([node, "-e", code], check=True, capture_output=True, text=True)
    assert json.loads(result.stdout) == pytest.approx([0, 1, 2, 2])
