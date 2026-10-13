# Songsterr octave notation

Preservation contract 118 accepts `beat.octaveClef` when absent, null, or exactly
`8va`, `8vb`, `15ma`, or `15mb`. Other values remain validation failures, including
false, zero and empty values on rests or excluded parts.

These are retained notation labels. Songsterr's explicit string, fret, tuning
and capo already identify the sounding pitch. Conversion must not transpose a
second time, change frets, add attacks, or change timing. The original field is
preserved unchanged in the source document and indexed as source metadata;
standard notation and the highway do not gain a new octave glyph. This routine
metadata does not produce a player limitation notice. Measure-level `clef`
continues to follow its separate existing rule.

The independent reader validates the enum separately. Package verification
checks every retained value and location, including nulls and excluded parts,
and rejects missing, duplicate, altered or falsely classified claims. Existing
pitch and timing comparisons still apply to original and Hybrid Lead charts.

## Evidence and scope

The public schema inspected on 2026-10-13 declares those four strings plus null.
The current synthesis worker derives note pitch from tuning, fret, capo and
transpose, without reading `octaveClef`. Its SHA-256 is
`a7f191f19196af972bd61ed5f9c67a6b63151fc8b0b8a1f665061a0cb493c980`;
the schema bundle SHA-256 is
`d019c795e1e9ee57d85e5c89d10cd33246df3819c3c0241008666d8149907ab0`.
These current assets were inspected statically, not installed or executed.

The qualified, frozen native reference worker
`4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab`
produced identical marked/unmarked scheduling within each authored and
player-defaults profile for real Lead Guitar/Strings data and synthetic enum,
tie and repeat cases. Fret/tuning positive controls changed the reference pitch.
This establishes this field's handling in the explicit Songsterr tab format;
it is not a general rule to ignore octave notation in other score formats.
