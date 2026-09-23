# Exact natural harmonic nodes

Integer natural nodes at frets 4, 5, 7, 9, 12, 16 and 19 use the existing
FeedPak `hm` cue at the unchanged authored string/fret. An explicit
`harmonicFret` must equal that fret. The sounding notation pitch is the open
string plus the source overtone and capo, not the fretted fundamental.
No new game cue or recognition policy is introduced.

For example, a natural harmonic at fret 7 on low E retains the fret-7 harmonic
instruction and has MIDI pitch 59 (E2 + 19 semitones), not 47 (E2 + 7).
Source data remains byte-for-byte preserved. Missing specialized harmonic
engraving in an optional notation surface remains a display limitation;
the game's existing harmonic instruction remains in the playable chart.

The node mapping agrees with both the public Songsterr worker's `Ui` table
and its common bundle harmonic pitch lookup, captured 2026-09-23. Worker hash:
`9bc2e262f42077e5f6d13d7c67269d8e92c2e24f8d6ff47ca9601b235251c33f`.
Common bundle hash:
`039a95156a0b966e7c3602ea8d0f4ae403266ec2f7ba930dd64fcbe41151fc29`.

Fractional nodes, differing touch/fretted positions, and richer harmonic types
are not collapsed into this mapping. Their precise instructions still require
consumer support and, where absent, presentation decisions. Pitch and cue-loss
mutation tests use a hand-authored archive independent of the converter.
