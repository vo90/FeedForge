"""SNG mute sentinel regression, reduced from I Shot the Sheriff chord 19."""
from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

from feedback_converter import converter as c


def _song(*, masks=None, parent_mask=c.NOTE_MASK_FRETHANDMUTE | c.NOTE_MASK_MUTE, second_mask=None):
    if masks is None:
        masks = [0, 0, 0, c.NOTE_MASK_MUTE, c.NOTE_MASK_MUTE, c.NOTE_MASK_MUTE]
    chord_notes = NS(
        mask=masks, bends=[NS(bendValues=[], count=0) for _ in range(6)],
        slideTo=[-1] * 6, slideUnpitchTo=[-1] * 6,
    )
    levels = []
    for difficulty, mask in enumerate([parent_mask, parent_mask if second_mask is None else second_mask]):
        levels.append(NS(difficulty=difficulty, anchors=[], fingerprints=[[], []], notes=[
            NS(time=10.105, sustain=0.0, chordId=0,
               chordNoteId=0 if difficulty == 0 else c.UINT32_NONE, mask=mask),
        ]))
    return NS(
        metadata=NS(tuning=[0] * 6, capo=0),
        chordTemplates=[NS(name=" ", mask=0, frets=[-1, -1, -1, 127, 127, 127], fingers=[-1] * 6)],
        chordNotes=[chord_notes], levels=levels,
        phraseIterations=[NS(phraseId=0, time=0.0, endTime=20.0)],
        phrases=[NS(name="verse", maxDifficulty=1)], beats=[], sections=[],
    )


def _arrangement(song):
    return c._song_to_arrangement(song, "songs/bin/generic/sheriff_lead.sng", {}, include_tones=False)


def test_muted_sentinel_normalizes_template_flat_and_every_difficulty_without_mutating_source():
    source = _song()
    before = deepcopy(source)
    arrangement = _arrangement(source)
    assert arrangement["templates"][0]["frets"] == [-1, -1, -1, 0, 0, 0]
    for chord in [arrangement["chords"][0], *[
        level["chords"][0] for level in arrangement["phrases"][0]["levels"]
    ]]:
        assert chord["t"] == 10.105
        assert chord["id"] == 0
        assert [(n["s"], n["f"]) for n in chord["notes"]] == [(3, 0), (4, 0), (5, 0)]
        assert all(n.get("mt") or n.get("fhm") for n in chord["notes"])
    assert source == before


def test_shared_template_requires_mute_evidence_on_every_use_including_other_difficulties():
    source = _song(second_mask=0)
    arrangement = _arrangement(source)
    # The unmuted source is ambiguous and must still fail normal bounds validation.
    assert arrangement["templates"][0]["frets"][3:] == [127, 127, 127]
    assert all(n["f"] == 127 for n in arrangement["chords"][0]["notes"])


def test_unreferenced_template_and_palm_mute_alone_do_not_authorize_sentinel_normalization():
    source = _song(masks=[c.NOTE_MASK_PALMMUTE] * 6, parent_mask=c.NOTE_MASK_PALMMUTE)
    source.chordTemplates.append(deepcopy(source.chordTemplates[0]))
    arrangement = _arrangement(source)
    assert all(t["frets"][3:] == [127, 127, 127] for t in arrangement["templates"])
    assert all(n["f"] == 127 for n in arrangement["chords"][0]["notes"])


@pytest.mark.parametrize("fret,mask,expected", [
    (127, c.NOTE_MASK_MUTE, 0), (127, c.NOTE_MASK_FRETHANDMUTE, 127),
    (127, c.NOTE_MASK_PALMMUTE, 127), (127, 0, 127),
    (25, c.NOTE_MASK_MUTE, 25), (7, c.NOTE_MASK_MUTE, 7), (0, c.NOTE_MASK_MUTE, 0),
])
def test_single_note_sentinel_requires_a_specific_mute_flag_and_preserves_valid_frets(fret, mask, expected):
    source = NS(time=1.25, string=2, fret=fret, mask=mask, sustain=0.0,
                slideTo=-1, slideUnpitchTo=-1, bend_time=0.0, bends=[], leftHand=-1)
    note = c._note_to_feedpak(source)
    assert note["f"] == expected
    assert note["t"] == 1.25 and note["s"] == 2
    if mask == c.NOTE_MASK_MUTE:
        assert note["mt"] is True
