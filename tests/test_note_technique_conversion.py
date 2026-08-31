import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from feedback_converter import converter


def _ns(**kwargs):
    return SimpleNamespace(**kwargs)


def _bend(time, step):
    return _ns(time=time, step=step)


def _note(**overrides):
    values = {
        "time": 12.5,
        "string": 2,
        "fret": 7,
        "sustain": 0.0,
        "slideTo": -1,
        "slideUnpitchTo": -1,
        "bend_time": 0.0,
        "bends": [],
        "leftHand": -1,
        "mask": 0,
    }
    values.update(overrides)
    return _ns(**values)


TECHNIQUE_MASKS = [
    ("hammer-on", converter.NOTE_MASK_HAMMERON, "ho"),
    ("pull-off", converter.NOTE_MASK_PULLOFF, "po"),
    ("tap", converter.NOTE_MASK_TAP, "tp"),
    ("natural harmonic", converter.NOTE_MASK_HARMONIC, "hm"),
    ("pinch harmonic", converter.NOTE_MASK_PINCHHARMONIC, "hp"),
    ("palm mute", converter.NOTE_MASK_PALMMUTE, "pm"),
    ("string mute", converter.NOTE_MASK_MUTE, "mt"),
    ("fret-hand mute", converter.NOTE_MASK_FRETHANDMUTE, "fhm"),
    ("tremolo", converter.NOTE_MASK_TREMOLO, "tr"),
    ("vibrato", converter.NOTE_MASK_VIBRATO, "vb"),
    ("slap", converter.NOTE_MASK_SLAP, "slp"),
    ("pluck", converter.NOTE_MASK_PLUCK, "plk"),
    ("accent", converter.NOTE_MASK_ACCENT, "ac"),
    ("link-next parent", converter.NOTE_MASK_PARENT, "ln"),
    ("ignore", converter.NOTE_MASK_IGNORE, "ig"),
]


@pytest.mark.parametrize(
    ("_description", "mask", "wire_key"),
    TECHNIQUE_MASKS,
    ids=[item[0] for item in TECHNIQUE_MASKS],
)
def test_sng_note_technique_masks_map_to_feedpak_wire_keys(
    _description, mask, wire_key
):
    converted = converter._note_to_feedpak(_note(mask=mask))

    assert converted == {"t": 12.5, "s": 2, "f": 7, wire_key: True}


def test_sng_note_core_fields_and_bend_shape_map_without_losing_precision():
    converted = converter._note_to_feedpak(
        _note(
            time=12.5,
            string=4,
            fret=9,
            sustain=0.875,
            slideTo=12,
            slideUnpitchTo=5,
            leftHand=3,
            bends=[
                _bend(12.5, 0.0),
                _bend(12.75, 1.0),
                _bend(13.0, 2.0),
                _bend(13.25, 1.0),
            ],
        )
    )

    assert converted == {
        "t": 12.5,
        "s": 4,
        "f": 9,
        "sus": 0.875,
        "sl": 12,
        "slu": 5,
        "bn": 2.0,
        "bnv": [
            {"t": 0.0, "v": 0.0},
            {"t": 0.25, "v": 1.0},
            {"t": 0.5, "v": 2.0},
            {"t": 0.75, "v": 1.0},
        ],
        "fg": 3,
    }


def _empty_chord_bends():
    return [_ns(bendValues=[], count=0) for _ in range(6)]


def test_explicit_chord_constituents_keep_per_string_fields_and_techniques():
    bends = _empty_chord_bends()
    bends[0] = _ns(
        bendValues=[
            _bend(4.0, 0.0),
            _bend(4.25, 0.5),
            _bend(4.5, 1.0),
        ],
        count=3,
    )
    chord_note = _ns(
        mask=[
            converter.NOTE_MASK_SUSTAIN
            | converter.NOTE_MASK_HAMMERON
            | converter.NOTE_MASK_HARMONIC,
            converter.NOTE_MASK_PULLOFF
            | converter.NOTE_MASK_TAP
            | converter.NOTE_MASK_PINCHHARMONIC
            | converter.NOTE_MASK_PALMMUTE
            | converter.NOTE_MASK_MUTE
            | converter.NOTE_MASK_FRETHANDMUTE
            | converter.NOTE_MASK_TREMOLO
            | converter.NOTE_MASK_VIBRATO
            | converter.NOTE_MASK_SLAP
            | converter.NOTE_MASK_PLUCK
            | converter.NOTE_MASK_IGNORE,
            0,
            0,
            0,
            0,
        ],
        slideTo=[8, -1, -1, -1, -1, -1],
        slideUnpitchTo=[-1, 3, -1, -1, -1, -1],
        bends=bends,
    )
    song = _ns(
        chordTemplates=[_ns(frets=[5, 7, -1, -1, -1, -1])],
        chordNotes=[chord_note],
    )
    chord = _ns(
        time=4.0,
        sustain=0.75,
        mask=converter.NOTE_MASK_ACCENT | converter.NOTE_MASK_PARENT,
        chordNoteId=0,
    )

    converted = converter._chord_notes(song, chord, chord_id=0)

    assert converted == [
        {
            "s": 0,
            "f": 5,
            "sus": 0.75,
            "sl": 8,
            "bn": 1.0,
            "bnv": [
                {"t": 0.0, "v": 0.0},
                {"t": 0.25, "v": 0.5},
                {"t": 0.5, "v": 1.0},
            ],
            "hm": True,
            "ac": True,
            "ln": True,
            "ho": True,
        },
        {
            "s": 1,
            "f": 7,
            "slu": 3,
            "pm": True,
            "mt": True,
            "fhm": True,
            "hp": True,
            "ac": True,
            "vb": True,
            "tr": True,
            "tp": True,
            "plk": True,
            "slp": True,
            "ln": True,
            "ig": True,
            "po": True,
        },
    ]


def _feedback_song_module():
    module_path = os.environ.get("FEEDBACK_SONG_MODULE")
    if not module_path:
        pytest.skip(
            "set FEEDBACK_SONG_MODULE to FeedBack's lib/song.py to run the "
            "cross-repository wire-contract check"
        )
    path = Path(module_path)
    if not path.is_file():
        pytest.fail(f"FEEDBACK_SONG_MODULE does not name a file: {path}")
    spec = importlib.util.spec_from_file_location("_feedback_song_contract", path)
    if spec is None or spec.loader is None:
        pytest.fail(f"could not load FeedBack song module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_converted_note_and_chord_survive_feedback_wire_round_trip():
    feedback_song = _feedback_song_module()
    source_note = _note(
        sustain=0.875,
        slideTo=12,
        slideUnpitchTo=5,
        leftHand=3,
        bends=[
            _bend(12.5, 0.0),
            _bend(12.75, 1.0),
            _bend(13.0, 2.0),
        ],
        mask=sum(mask for _description, mask, _wire_key in TECHNIQUE_MASKS),
    )
    note_wire = converter._note_to_feedpak(source_note)

    decoded_note = feedback_song.note_from_wire(note_wire)
    round_tripped_note = feedback_song.note_to_wire(decoded_note)

    assert feedback_song.note_from_wire(round_tripped_note) == decoded_note
    assert all(round_tripped_note[key] == value for key, value in note_wire.items())

    chord_wire = {
        "t": 12.5,
        "id": 0,
        "notes": [dict(note_wire, t=None)],
    }
    chord_wire["notes"][0].pop("t")
    decoded_chord = feedback_song.chord_from_wire(chord_wire)
    round_tripped_chord = feedback_song.chord_to_wire(decoded_chord)

    assert feedback_song.chord_from_wire(round_tripped_chord) == decoded_chord
    assert all(
        round_tripped_chord["notes"][0][key] == value
        for key, value in chord_wire["notes"][0].items()
    )
