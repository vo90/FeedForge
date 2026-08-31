from types import SimpleNamespace

import pytest

from feedback_converter import converter


def _ns(**kwargs):
    return SimpleNamespace(**kwargs)


def _empty_bends():
    return [_ns(bendValues=[], count=0) for _ in range(6)]


def _song(*, chord_note_masks=None):
    chord_notes = []
    if chord_note_masks is not None:
        chord_notes.append(
            _ns(
                mask=chord_note_masks,
                slideTo=[-1, -1, -1, -1, -1, -1],
                slideUnpitchTo=[-1, -1, -1, -1, -1, -1],
                bends=_empty_bends(),
            )
        )
    return _ns(
        chordTemplates=[_ns(frets=[-1, 3, 2, 0, 1, -1])],
        chordNotes=chord_notes,
    )


def _chord(*, sustain, chord_note_id, mask=0):
    return _ns(
        time=2.0,
        sustain=sustain,
        mask=mask,
        chordId=0,
        chordNoteId=chord_note_id,
    )


def test_chord_sustain_is_emitted_only_for_strings_marked_to_sustain():
    song = _song(
        chord_note_masks=[
            0,
            converter.NOTE_MASK_SUSTAIN | converter.NOTE_MASK_HARMONIC,
            converter.NOTE_MASK_PALMMUTE,
            converter.NOTE_MASK_SUSTAIN,
            0,
            0,
        ]
    )

    notes = converter._chord_notes(song, _chord(sustain=1.25, chord_note_id=0), 0)

    assert notes == [
        {"s": 1, "f": 3, "sus": 1.25, "hm": True},
        {"s": 2, "f": 2, "pm": True},
        {"s": 3, "f": 0, "sus": 1.25},
        {"s": 4, "f": 1},
    ]


def test_chord_sustain_mask_cannot_create_a_duration_missing_from_parent():
    song = _song(
        chord_note_masks=[
            0,
            converter.NOTE_MASK_SUSTAIN,
            converter.NOTE_MASK_SUSTAIN,
            converter.NOTE_MASK_SUSTAIN,
            converter.NOTE_MASK_SUSTAIN,
            0,
        ]
    )

    notes = converter._chord_notes(song, _chord(sustain=0.0, chord_note_id=0), 0)

    assert all("sus" not in note for note in notes)


@pytest.mark.parametrize("chord_note_id", [converter.UINT32_NONE, 1, -1])
def test_missing_chord_note_data_uses_parent_sustain_for_active_strings(chord_note_id):
    song = _song()

    notes = converter._chord_notes(song, _chord(sustain=0.75, chord_note_id=chord_note_id), 0)

    assert notes == [
        {"s": 1, "f": 3, "sus": 0.75},
        {"s": 2, "f": 2, "sus": 0.75},
        {"s": 3, "f": 0, "sus": 0.75},
        {"s": 4, "f": 1, "sus": 0.75},
    ]


def test_missing_chord_note_data_without_parent_sustain_omits_sustain():
    song = _song()

    notes = converter._chord_notes(
        song,
        _chord(
            sustain=0.0,
            chord_note_id=converter.UINT32_NONE,
            mask=converter.NOTE_MASK_PALMMUTE,
        ),
        0,
    )

    assert notes == [
        {"s": 1, "f": 3, "pm": True},
        {"s": 2, "f": 2, "pm": True},
        {"s": 3, "f": 0, "pm": True},
        {"s": 4, "f": 1, "pm": True},
    ]
