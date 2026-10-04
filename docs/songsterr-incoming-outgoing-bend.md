# Bends with incoming and outgoing slide cues

Preservation contract 70 qualifies a direction-only slide-in on the initial
attack and a direction-only slide-out on the last tied segment of one fretted
note. The bend retains its authored tie/segment clock, including tempo changes.
Both visual cues remain present; no approach fret, destination fret, extra attack
or finger release is invented. Note vibrato retains its own source intervals.

Both cues must belong to the same held fret/string. The final segment must end
at the articulation end. Bend controllers must not overlap. Later incoming cues,
nonterminal outgoing cues, targeted slides, harmonics, whammy, beat vibrato,
HO/PO, trills, scrapes, mutes, let-ring, displaced attacks and tied staccato are
outside this qualification. Existing single-cue and settled-handoff rules stay
unchanged. This is a general source-shape rule, without song or revision IDs.

The native synth can change sounding attack/end times to generate slide sounds,
depending on the instrument program. Those synthesis details do not change the
written playable note boundaries or compress its finger-bend gesture.

The producer and independent rational verifier qualify this composition
separately. Finger-bend evidence v11 records both `initialSlideIn` and
`terminalSlideOut`; packages using the composition reject contracts below 70.
Original source bytes are retained. Previously imported files are not migrated.

Regression fixtures contain 41 synthetic sources, qualified against the pinned
public Songsterr worker (SHA-256
`4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab`).
They cover both slide directions, guitar/bass programs, tempo changes, ties,
repeats, chords, following slide-ins, note vibrato and excluded combinations.
Each eligible source has four diagnostic variants and both authored/default
native profiles. Pitch comparison allows native sampling quantization only;
source qualification uses exact written boundaries. Package checks include
Hybrid Lead, piecewise alignment, contract downgrades and deliberate corruption
of each cue, bend curve, timing, source evidence and note data.
