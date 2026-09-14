from __future__ import annotations

import pytest

import feedback_converter.rocksmith_xml as rocksmith_xml
from feedback_converter.converter import _song_to_arrangement, _song_to_timeline
from feedback_converter.rocksmith_xml import (
    NOTE_MASK_HAMMERON,
    NOTE_MASK_HIGHDENSITY,
    NOTE_MASK_PALMMUTE,
    RocksmithChordTemplateHint,
    RocksmithXmlError,
    UINT32_NONE,
    parse_rocksmith_arrangement_xml,
)


def _arrangement_xml(
    *,
    arrangement: str = "Combo",
    chord_templates: str = """
      <chordTemplate displayName="A" chordName="A"
        fret0="-1" fret1="0" fret2="2" fret3="2" fret4="2" fret5="-1"
        finger0="-1" finger1="-1" finger2="1" finger3="2" finger4="3" finger5="-1" />
    """,
    chords: str = """
      <chord time="1.5" chordId="0" highDensity="0">
        <chordNote time="1.5" string="1" fret="0" sustain="0.25" palmMute="1" />
        <chordNote time="1.5" string="2" fret="2" sustain="0.25" />
        <chordNote time="1.5" string="3" fret="2" sustain="0.25" />
      </chord>
      <chord time="2.5" chordId="1" highDensity="1">
        <chordNote time="2.5" string="1" fret="0" sustain="0" />
      </chord>
    """,
    tone_event: str = '<tone time="2" name="Crunch" />',
    hand_shape_id: int = 1,
) -> bytes:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<song version="7">
  <title>Example Song</title>
  <arrangement>{arrangement}</arrangement>
  <part>1</part>
  <offset>-10</offset>
  <centOffset>15</centOffset>
  <songLength>4</songLength>
  <tuning string0="0" string1="0" string2="0" string3="0" string4="0" string5="0" />
  <capo>0</capo>
  <artistName>Example Artist</artistName>
  <albumName>Example Album</albumName>
  <albumYear>2026</albumYear>
  <arrangementProperties pathRhythm="1" routeMask="2" />
  <lastConversionDateTime>09-01-26 12:00</lastConversionDateTime>
  <phrases>
    <phrase disparity="0" ignore="0" maxDifficulty="1" name="VERSE" solo="0" />
  </phrases>
  <phraseIterations>
    <phraseIteration time="0" phraseId="0">
      <heroLevels><heroLevel difficulty="1" hero="1" /></heroLevels>
    </phraseIteration>
  </phraseIterations>
  <chordTemplates>{chord_templates}</chordTemplates>
  <ebeats>
    <ebeat time="0" measure="0" />
    <ebeat time="1" measure="1" />
    <ebeat time="2" measure="-1" />
  </ebeats>
  <tonebase>Clean</tonebase>
  <tonea>Clean</tonea>
  <toneb>Crunch</toneb>
  <tonec></tonec>
  <toned></toned>
  <tones>{tone_event}</tones>
  <events><event time="1" code="dna_riff" /></events>
  <sections><section name="verse" number="1" startTime="0" /></sections>
  <levels>
    <level difficulty="0">
      <notes>
        <note time="1" string="0" fret="3" sustain="0.5" hammerOn="1" bend="1">
          <bendValues>
            <bendValue time="1.1" step="0" unk5="0" />
            <bendValue time="1.3" step="1" unk5="0" />
          </bendValues>
        </note>
      </notes>
      <chords>{chords}</chords>
      <anchors><anchor time="0.5" fret="1" width="4" /></anchors>
      <handShapes><handShape chordId="{hand_shape_id}" startTime="2.5" endTime="3" /></handShapes>
    </level>
    <level difficulty="1">
      <notes>
        <note time="1" string="0" fret="5" sustain="0" />
      </notes>
      <chords>{chords}</chords>
      <anchors><anchor time="0.5" fret="3" width="4" /></anchors>
      <handShapes><handShape chordId="{hand_shape_id}" startTime="1.5" endTime="2" /></handShapes>
    </level>
  </levels>
</song>
""".encode("utf-8")


def _parse(data: bytes | None = None, **overrides):
    return parse_rocksmith_arrangement_xml(
        data or _arrangement_xml(),
        source_path=overrides.pop("source_path", "songs/arr/example_combo.xml"),
        expected_song_key=overrides.pop("expected_song_key", "Example"),
        expected_arrangement=overrides.pop("expected_arrangement", "Combo"),
        **overrides,
    )


def test_xml_adapter_preserves_chart_and_is_converter_helper_compatible() -> None:
    full_a = RocksmithChordTemplateHint(
        frets=(-1, 0, 2, 2, 2, -1),
        fingers=(-1, -1, 1, 2, 3, -1),
        name="A",
    )
    song = _parse(compiled_chord_templates={1: full_a})

    assert song.song_key == "Example"
    assert song.arrangement == "Combo"
    assert song.title == "Example Song"
    assert song.artist == "Example Artist"
    assert song.metadata.songLength == 4.0
    assert song.metadata.tuning == (0, 0, 0, 0, 0, 0)
    assert song.cent_offset == 15.0
    assert len(song.beats) == 3
    assert len(song.phrases) == 1
    assert len(song.phraseIterations) == 1
    assert song.phraseIterations[0].endTime == 4.0
    assert len(song.sections) == 1
    assert len(song.levels) == 2
    assert sum(len(level.anchors) for level in song.levels) == 2
    assert sum(sum(len(group) for group in level.fingerprints) for level in song.levels) == 2

    # Referencing ID 1 while declaring one master identifies compiled-ID XML.
    # Both referenced IDs are resolved without indexing the single master.
    # The manifest hint authoritatively restores compiled ID 1's full shape.
    assert len(song.chordTemplates) == 2
    assert [template.name for template in song.chordTemplates] == ["", "A"]
    assert song.approximate_chord_template_ids == (0,)
    assert song.chordTemplates[0].frets == (-1, 0, 2, 2, -1, -1)
    assert song.chordTemplates[1].frets == (-1, 0, 2, 2, 2, -1)

    level = song.levels[0]
    plain_note = next(note for note in level.notes if note.chordId == UINT32_NONE)
    assert plain_note.mask & NOTE_MASK_HAMMERON
    assert [bend.step for bend in plain_note.bends] == [0.0, 1.0]
    chords = [note for note in level.notes if note.chordId != UINT32_NONE]
    assert [chord.chordId for chord in chords] == [0, 1]
    assert chords[1].mask & NOTE_MASK_HIGHDENSITY
    chord_note = song.chordNotes[chords[0].chordNoteId]
    assert chord_note.mask[1] & NOTE_MASK_PALMMUTE

    # Name-only tone events resolve against tonea..toned and preserve the name.
    assert song.tone_base == "Clean"
    assert song.tone_slots == ("Clean", "Crunch", "", "")
    assert [(tone.time, tone.id, tone.name) for tone in song.tones] == [(2.0, 1, "Crunch")]

    arrangement = _song_to_arrangement(
        song,
        song.source_path,
        {},
        include_tones=False,
        cent_offset=song.cent_offset,
        arrangement_id="rhythm",
    )
    assert arrangement["name"] == "Rhythm"
    assert arrangement["centOffset"] == 15.0
    assert len(arrangement["templates"]) == 2
    assert len(arrangement["beats"]) == 3
    assert len(arrangement["sections"]) == 1
    assert len(arrangement["phrases"]) == 1
    assert _song_to_timeline(song) == {
        "version": 1,
        "beats": arrangement["beats"],
        "sections": arrangement["sections"],
    }


def test_xml_adapter_accepts_direct_master_ids_without_child_notes() -> None:
    song = _parse(
        _arrangement_xml(
            chords='<chord time="2" chordId="0" highDensity="0" />',
            tone_event='<tone time="2" id="0" name="Clean" />',
            hand_shape_id=0,
        )
    )

    assert len(song.chordTemplates) == 1
    assert song.approximate_chord_template_ids == ()
    chord = next(note for note in song.levels[0].notes if note.chordId != UINT32_NONE)
    assert chord.chordId == 0
    assert chord.chordNoteId == UINT32_NONE
    assert song.tones[0].id == 0


def test_xml_adapter_accepts_direct_master_id_with_compatible_child_subset() -> None:
    song = _parse(
        _arrangement_xml(
            chords="""
              <chord time="2" chordId="0">
                <chordNote time="2" string="1" fret="0" />
                <chordNote time="2" string="2" fret="2" />
              </chord>
            """,
            hand_shape_id=0,
        )
    )

    assert song.chordTemplates[0].frets == (-1, 0, 2, 2, 2, -1)
    assert song.approximate_chord_template_ids == ()


def test_xml_adapter_rejects_direct_master_id_with_incompatible_child_shape() -> None:
    data = _arrangement_xml(
        chords="""
          <chord time="2" chordId="0">
            <chordNote time="2" string="0" fret="9" />
          </chord>
        """,
        hand_shape_id=0,
    )

    with pytest.raises(
        RocksmithXmlError,
        match="conflicts with its indexed XML master template",
    ):
        _parse(data)


def test_xml_adapter_rejects_negative_chord_child_fret() -> None:
    data = _arrangement_xml(
        chords="""
          <chord time="2" chordId="0">
            <chordNote time="2" string="1" fret="-1" />
          </chord>
        """,
        hand_shape_id=0,
    )

    with pytest.raises(RocksmithXmlError, match="Chord note fret cannot be negative"):
        _parse(data)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"expected_song_key": "Different"}, "SongKey mismatch"),
        ({"expected_arrangement": "Lead"}, "Arrangement mismatch"),
        ({"source_path": "songs/arr/example_lead.xml"}, "Arrangement/path mismatch"),
    ],
)
def test_xml_adapter_rejects_identity_mismatches(kwargs: dict[str, str], message: str) -> None:
    with pytest.raises(RocksmithXmlError, match=message):
        _parse(**kwargs)


@pytest.mark.parametrize(
    "declaration",
    [
        '<!DOCTYPE song SYSTEM "https://example.invalid/rocksmith.dtd">',
        '<!DOCTYPE song [<!ENTITY payload SYSTEM "file:///windows/win.ini">]>',
        '<!ENTITY payload "expanded">',
    ],
)
def test_xml_adapter_rejects_dtd_and_entity_declarations(declaration: str) -> None:
    data = _arrangement_xml().replace(b'<song version="7">', f'{declaration}<song version="7">'.encode())

    with pytest.raises(RocksmithXmlError, match="DTD or entity declarations"):
        _parse(data)


def test_streaming_xml_parser_enforces_node_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rocksmith_xml, "MAX_XML_NODES", 10)

    with pytest.raises(RocksmithXmlError, match=r"too many nodes \(11 > 10\)"):
        _parse()


def test_streaming_xml_parser_enforces_depth_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rocksmith_xml, "MAX_XML_DEPTH", 3)

    with pytest.raises(RocksmithXmlError, match=r"too deeply nested \(4 > 3\)"):
        _parse()


def test_compiled_chord_ids_reject_conflicting_full_shapes() -> None:
    conflicting = """
      <chord time="1" chordId="1"><chordNote time="1" string="1" fret="0" /></chord>
      <chord time="2" chordId="1"><chordNote time="2" string="1" fret="2" /></chord>
    """

    with pytest.raises(RocksmithXmlError, match="conflicting equally complete"):
        _parse(_arrangement_xml(chords=conflicting, hand_shape_id=1))


def test_compiled_chord_ids_reject_ambiguous_master_match() -> None:
    masters = """
      <chordTemplate displayName="A" chordName="A"
        fret0="-1" fret1="0" fret2="2" fret3="2" fret4="2" fret5="-1"
        finger0="-1" finger1="-1" finger2="1" finger3="2" finger4="3" finger5="-1" />
      <chordTemplate displayName="Am7" chordName="Am7"
        fret0="-1" fret1="0" fret2="2" fret3="2" fret4="2" fret5="-1"
        finger0="-1" finger1="-1" finger2="2" finger3="3" finger4="4" finger5="-1" />
    """
    compiled = """
      <chord time="1" chordId="2">
        <chordNote time="1" string="1" fret="0" />
        <chordNote time="1" string="2" fret="2" />
        <chordNote time="1" string="3" fret="2" />
        <chordNote time="1" string="4" fret="2" />
      </chord>
    """

    with pytest.raises(RocksmithXmlError, match="ambiguously matches multiple master templates"):
        _parse(_arrangement_xml(chord_templates=masters, chords=compiled, hand_shape_id=2))


def test_compiled_chord_ids_never_fabricate_missing_shape() -> None:
    chords = """
      <chord time="1" chordId="0"><chordNote time="1" string="1" fret="0" /></chord>
      <chord time="2" chordId="1" />
    """

    with pytest.raises(RocksmithXmlError, match="no child-note shape; refusing to fabricate"):
        _parse(_arrangement_xml(chords=chords))


def test_target_shaped_hints_resolve_seven_ids_and_report_approximations() -> None:
    masters = """
      <chordTemplate displayName="A" chordName="A"
        fret0="-1" fret1="0" fret2="2" fret3="2" fret4="2" fret5="-1"
        finger0="-1" finger1="-1" finger2="1" finger3="1" finger4="1" finger5="-1" />
      <chordTemplate displayName="E" chordName="E"
        fret0="0" fret1="2" fret2="2" fret3="1" fret4="0" fret5="0"
        finger0="-1" finger1="2" finger2="3" finger3="1" finger4="-1" finger5="-1" />
      <chordTemplate displayName="B" chordName="B"
        fret0="-1" fret1="2" fret2="4" fret3="4" fret4="4" fret5="-1"
        finger0="-1" finger1="1" finger2="3" finger3="3" finger4="3" finger5="-1" />
    """
    chords = """
      <chord time="0.1" chordId="0">
        <chordNote time="0.1" string="0" fret="0" />
        <chordNote time="0.1" string="1" fret="2" leftHand="2" />
      </chord>
      <chord time="0.2" chordId="1">
        <chordNote time="0.2" string="1" fret="0" />
      </chord>
      <chord time="0.3" chordId="2">
        <chordNote time="0.3" string="1" fret="2" leftHand="1" />
        <chordNote time="0.3" string="2" fret="4" leftHand="3" />
      </chord>
      <chord time="0.4" chordId="3">
        <chordNote time="0.4" string="0" fret="0" />
        <chordNote time="0.4" string="1" fret="2" />
        <chordNote time="0.4" string="2" fret="2" />
        <chordNote time="0.4" string="3" fret="1" />
        <chordNote time="0.4" string="4" fret="0" />
        <chordNote time="0.4" string="5" fret="0" />
      </chord>
      <chord time="0.5" chordId="4">
        <chordNote time="0.5" string="1" fret="2" />
        <chordNote time="0.5" string="2" fret="4" />
        <chordNote time="0.5" string="3" fret="4" />
        <chordNote time="0.5" string="4" fret="4" />
      </chord>
      <chord time="0.6" chordId="5">
        <chordNote time="0.6" string="1" fret="0" />
        <chordNote time="0.6" string="2" fret="2" />
      </chord>
      <chord time="0.7" chordId="6">
        <chordNote time="0.7" string="1" fret="0" />
      </chord>
    """
    hints = {
        1: RocksmithChordTemplateHint(
            frets=(-1, 0, 2, 2, 2, -1),
            fingers=(-1, -1, 1, 1, 1, -1),
            name="A",
        ),
        3: RocksmithChordTemplateHint(
            frets=(0, 2, 2, 1, 0, 0),
            fingers=(-1, 2, 3, 1, -1, -1),
            name="E",
        ),
        4: RocksmithChordTemplateHint(
            frets=(-1, 2, 4, 4, 4, -1),
            fingers=(-1, 1, 3, 3, 3, -1),
            name="B",
        ),
    }

    song = _parse(
        _arrangement_xml(
            chord_templates=masters,
            chords=chords,
            hand_shape_id=6,
        ),
        compiled_chord_templates=hints,
    )

    assert len(song.chordTemplates) == 7
    assert song.approximate_chord_template_ids == (0, 2, 5, 6)
    assert [template.name for template in song.chordTemplates] == ["", "A", "", "E", "B", "", ""]
    assert [template.frets for template in song.chordTemplates] == [
        (0, 2, -1, -1, -1, -1),
        (-1, 0, 2, 2, 2, -1),
        (-1, 2, 4, -1, -1, -1),
        (0, 2, 2, 1, 0, 0),
        (-1, 2, 4, 4, 4, -1),
        (-1, 0, 2, -1, -1, -1),
        (-1, 0, -1, -1, -1, -1),
    ]
    assert sorted(
        {note.chordId for level in song.levels for note in level.notes if note.chordId != UINT32_NONE}
    ) == list(range(7))


def test_manifest_chord_hint_must_agree_with_xml_child_samples() -> None:
    incompatible = RocksmithChordTemplateHint(
        frets=(-1, 2, 4, 4, 4, -1),
        fingers=(-1, 1, 3, 3, 3, -1),
        name="B",
    )

    with pytest.raises(RocksmithXmlError, match="conflicts with an authored child-note sample"):
        _parse(compiled_chord_templates={1: incompatible})


def test_absent_hint_mask_inherits_unique_matching_xml_master_arpeggio() -> None:
    arpeggio_master = """
      <chordTemplate displayName="A" chordName="A" arpeggio="1"
        fret0="-1" fret1="0" fret2="2" fret3="2" fret4="2" fret5="-1"
        finger0="-1" finger1="-1" finger2="1" finger3="2" finger4="3" finger5="-1" />
    """
    hint = RocksmithChordTemplateHint(
        frets=(-1, 0, 2, 2, 2, -1),
        fingers=(-1, -1, 1, 2, 3, -1),
        name="A",
    )

    song = _parse(
        _arrangement_xml(chord_templates=arpeggio_master),
        compiled_chord_templates={1: hint},
    )

    assert song.chordTemplates[1].mask == 1
    assert song.approximate_chord_template_ids == (0,)


def test_explicit_zero_hint_mask_overrides_matching_xml_master_arpeggio() -> None:
    arpeggio_master = """
      <chordTemplate displayName="A" chordName="A" arpeggio="1"
        fret0="-1" fret1="0" fret2="2" fret3="2" fret4="2" fret5="-1"
        finger0="-1" finger1="-1" finger2="1" finger3="2" finger4="3" finger5="-1" />
    """
    hint = RocksmithChordTemplateHint(
        frets=(-1, 0, 2, 2, 2, -1),
        fingers=(-1, -1, 1, 2, 3, -1),
        name="A",
        mask=0,
    )

    song = _parse(
        _arrangement_xml(chord_templates=arpeggio_master),
        compiled_chord_templates={1: hint},
    )

    assert song.chordTemplates[1].mask == 0


def test_absent_hint_mask_stays_zero_without_unique_fret_and_name_match() -> None:
    hint = RocksmithChordTemplateHint(
        frets=(-1, 0, 2, 2, 2, -1),
        fingers=(-1, -1, 1, 2, 3, -1),
        name="Different name",
    )

    song = _parse(compiled_chord_templates={1: hint})

    assert song.chordTemplates[1].mask == 0


def test_name_only_unknown_tones_receive_stable_ids() -> None:
    data = _arrangement_xml(
        tone_event="""
          <tone time="1" name="Custom Lead" />
          <tone time="2" name="Custom Lead" />
          <tone time="3" name="Custom FX" />
        """
    )
    song = _parse(data)

    assert [(tone.name, tone.id) for tone in song.tones] == [
        ("Custom Lead", 2),
        ("Custom Lead", 2),
        ("Custom FX", 3),
    ]
