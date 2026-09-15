"""Absolute SNG bend coordinates, including source-authored boundary errors."""
from types import SimpleNamespace as NS

import pytest

from feedback_converter import converter as c


def _note(time=225.113, sustain=0.193, points=()):
    return NS(time=time, sustain=sustain, string=3, fret=16, mask=0,
              slideTo=-1, slideUnpitchTo=-1, bend_time=2.0, leftHand=-1,
              bends=[NS(time=t, step=v) for t, v in points])


def test_anthrax_absolute_times_do_not_switch_coordinate_system_per_point():
    note = c._note_to_feedpak(_note(points=[(225.073, 1), (225.122, 1), (225.170, 0)]))
    assert note["bnv"] == [{"t": 0.0, "v": 1.0}, {"t": 0.009, "v": 1.0}, {"t": 0.057, "v": 0.0}]
    assert note["sus"] == 0.193


def test_pre_onset_segment_is_interpolated_at_onset_not_shifted():
    note = c._note_to_feedpak(_note(time=250.653, sustain=2.193,
        points=[(250.613, 0), (251.710, 1), (252.807, 2)]))
    assert note["bnv"] == [
        {"t": 0.0, "v": pytest.approx(0.04 / 1.097, abs=1e-6)},
        {"t": 1.057, "v": 1.0}, {"t": 2.154, "v": 2.0},
    ]


def test_entirely_pre_onset_curve_preserves_last_authored_value_as_held():
    note = c._note_to_feedpak(_note(time=271.498, sustain=0.139,
        points=[(271.458, 0), (271.493, 2)]))
    assert note["bnv"] == [{"t": 0.0, "v": 2.0}]
    assert note["bn"] == 2.0


def test_curve_released_before_onset_does_not_leave_a_phantom_scalar_bend():
    note = c._note_to_feedpak(_note(time=10.0, sustain=1.0, points=[(9.8, 2), (9.9, 0)]))
    assert "bn" not in note and "bnv" not in note


def test_sustain_overrun_is_interpolated_without_extending_note_or_forcing_release():
    note = c._note_to_feedpak(_note(time=10.0, sustain=0.5, points=[(10.0, 0), (11.0, 2)]))
    assert note["bnv"] == [{"t": 0.0, "v": 0.0}, {"t": 0.5, "v": 1.0}]
    assert note["bn"] == 1.0 and note["sus"] == 0.5


def test_valid_prebend_and_ordinary_points_keep_their_relative_shape():
    for points, expected in [
        ([(86.739, 2), (86.955, 0)], [(0, 2), (0.216, 0)]),
        ([(86.739, 0), (86.9, 1), (87.0, 0)], [(0, 0), (0.161, 1), (0.261, 0)]),
    ]:
        note = c._note_to_feedpak(_note(time=86.739, sustain=0.325, points=points))
        assert note["bnv"] == [{"t": t, "v": v} for t, v in expected]


def test_unsorted_or_nonfinite_source_curve_is_rejected_instead_of_guessed():
    for points in [[(10.2, 1), (10.1, 0)], [(10.0, 0), (float('nan'), 1)]]:
        with pytest.raises(ValueError):
            c._note_to_feedpak(_note(time=10.0, sustain=1.0, points=points))


def test_chord_bend_uses_the_same_source_coordinates_and_per_string_sustain():
    source = _note(points=[(225.073, 1), (225.122, 1), (225.170, 0)])
    chord_note = NS(mask=[c.NOTE_MASK_SUSTAIN, 0, 0, 0, 0, 0],
        bends=[NS(bendValues=source.bends, count=3)] + [NS(bendValues=[], count=0) for _ in range(5)],
        slideTo=[-1] * 6, slideUnpitchTo=[-1] * 6)
    song = NS(chordTemplates=[NS(frets=[16, 18, -1, -1, -1, -1])], chordNotes=[chord_note])
    chord = NS(time=source.time, sustain=source.sustain, mask=0, chordNoteId=0)
    notes = c._chord_notes(song, chord, 0)
    assert notes[0]["bnv"] == c._note_to_feedpak(source)["bnv"]
    assert notes[1] == {"s": 1, "f": 18}


def test_exceptional_source_normalization_is_reported_to_conversion_caller():
    source = _note(time=10.0, sustain=1.0, points=[(9.8, 0), (9.9, 2)])
    source.chordId = c.UINT32_NONE
    song = NS(metadata=NS(tuning=[0] * 6, capo=0), chordTemplates=[], chordNotes=[],
        levels=[NS(difficulty=0, notes=[source], anchors=[], fingerprints=[[], []])],
        phraseIterations=[], phrases=[], beats=[], sections=[])
    details = []
    c._song_to_arrangement(
        song,
        "example_lead.sng",
        {},
        include_tones=False,
        conversion_details=details,
    )
    assert len(details) == 1
    assert details[0].category == "source-normalization"
    assert "entirely pre-onset bends were held at their last authored value" in details[0].message
