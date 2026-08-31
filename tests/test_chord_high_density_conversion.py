from types import SimpleNamespace

import pytest

from feedback_converter import converter


def _ns(**kwargs):
    return SimpleNamespace(**kwargs)


def _chord(*, mask=0, sustain=0.0, chord_note_id=converter.UINT32_NONE):
    return _ns(
        time=1.0,
        string=-1,
        fret=-1,
        sustain=sustain,
        mask=mask,
        chordId=0,
        chordNoteId=chord_note_id,
    )


def _level(*notes, difficulty=0):
    return _ns(
        difficulty=difficulty,
        notes=list(notes),
        anchors=[],
        fingerprints=[[], []],
    )


def _song(*levels, chord_notes=None):
    return _ns(
        chordTemplates=[_ns(frets=[3, 5, -1, -1, -1, -1])],
        chordNotes=list(chord_notes or []),
        levels=list(levels),
        phraseIterations=[],
        phrases=[],
    )


@pytest.mark.parametrize(
    ("mask", "expected"),
    [
        (0, {"t": 1.0, "id": 0, "notes": [{"s": 0, "f": 3}, {"s": 1, "f": 5}]}),
        (
            converter.NOTE_MASK_HIGHDENSITY,
            {"t": 1.0, "id": 0, "hd": True, "notes": [{"s": 0, "f": 3}, {"s": 1, "f": 5}]},
        ),
    ],
)
def test_chord_high_density_is_true_or_omitted(mask, expected):
    level = _level(_chord(mask=mask))
    song = _song(level)

    _notes, chords = converter._notes_and_chords(song, level, [{"name": "C"}])

    assert chords == [expected]


def test_high_density_coexists_with_per_string_sustain_and_techniques():
    chord_note = _ns(
        mask=[
            converter.NOTE_MASK_SUSTAIN | converter.NOTE_MASK_HARMONIC,
            converter.NOTE_MASK_PALMMUTE,
            0,
            0,
            0,
            0,
        ],
        slideTo=[-1, -1, -1, -1, -1, -1],
        slideUnpitchTo=[-1, -1, -1, -1, -1, -1],
        bends=[_ns(bendValues=[], count=0) for _ in range(6)],
    )
    level = _level(
        _chord(mask=converter.NOTE_MASK_HIGHDENSITY, sustain=0.5, chord_note_id=0)
    )
    song = _song(level, chord_notes=[chord_note])

    _notes, chords = converter._notes_and_chords(song, level, [{"name": "C"}])

    assert chords == [
        {
            "t": 1.0,
            "id": 0,
            "hd": True,
            "notes": [
                {"s": 0, "f": 3, "sus": 0.5, "hm": True},
                {"s": 1, "f": 5, "pm": True},
            ],
        }
    ]


def test_duplicate_high_density_chord_records_remain_one_chord():
    chord = _chord(mask=converter.NOTE_MASK_HIGHDENSITY)
    level = _level(chord, chord)
    song = _song(level)

    _notes, chords = converter._notes_and_chords(song, level, [{"name": "C"}])

    assert len(chords) == 1
    assert chords[0]["hd"] is True


def test_song_chart_data_preserves_high_density_in_flat_and_phrase_levels():
    low = _level(_chord(), difficulty=0)
    high = _level(_chord(mask=converter.NOTE_MASK_HIGHDENSITY), difficulty=1)
    song = _song(low, high)
    song.phraseIterations = [_ns(phraseId=0, time=0.0, endTime=2.0)]
    song.phrases = [_ns(maxDifficulty=1)]

    chart = converter._song_chart_data(song, [{"name": "C"}])

    assert chart["chords"][0]["hd"] is True
    phrase_levels = chart["phrases"][0]["levels"]
    assert "hd" not in phrase_levels[0]["chords"][0]
    assert phrase_levels[1]["chords"][0]["hd"] is True
