# Finger bends with written beat vibrato and a terminal slide

Preservation contract 77, finger-bend evidence 17.

The converter qualifies the bend clock separately from uncertain beat-level
vibrato playback. This applies to continuous same-fret/string/voice fretted
ties, non-overlapping bend controls and one terminal direction-only slide.
Existing exclusions for other pitch expressions and displaced attacks remain.
The bend follows the completed source tie clock with the written control
points. The attack, sustain, vibrato intervals and slide-out remain unchanged.

`terminalSlideOut.beatVibrato` records the written fallback segments and the
`independent-written-instruction` policy. The independent source verifier
reconstructs eligibility, timing and provenance; it requires contract 77 and
evidence 17 for this composition. Beat-vibrato compatibility findings remain
required. The change does not claim verified vibrato synthesis or convert a
beat flag into a note flag.

The pinned Songsterr worker ignores these beat flags without changing bend
events in both tested profiles. Synthetic reference variants cover guitar,
bass, both directions, both intensities and initial/tied/mixed placement.
The continuous written curve intentionally does not copy MIDI quantization
or the worker's one-position interpolation offset between successive controls.
No gameplay, scoring or renderer rule changes are needed.

Regression coverage includes independent reconstruction, tempo/repeat/chord
contexts, rejected compositions, alignment maps, Hybrid Lead, and package
mutations of bends, vibrato, slide cues, note identity, evidence and warnings.
Existing imported files are not rewritten; affected songs require reimport.
