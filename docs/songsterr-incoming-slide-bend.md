# Initial slide-in with a finger bend

Preservation contract 67 qualifies an initial direction-only slide-in beside an
otherwise supported finger bend. The approach cue stays at the authored attack;
the bend uses the completed source tie clock, including source tempo changes.
No approach fret, pitch curve, attack, release or extra scored note is invented.
An incoming cue at score time zero does not delay the note to imitate a synth.

Eligibility requires the incoming cue on the first segment, a fretted note, no
outgoing/targeted slides, later incoming cues, whammy, displaced attacks,
overlapping bend controllers or other unqualified expressions. Independently
timed finger vibrato remains supported. A constant artificial harmonic also
retains its existing target throughout the tie; a changing harmonic is guarded.
Deferred cases retain their existing
segment timing and conversion finding. Tied staccato retains its separate rule.

The producer and independent rational verifier qualify this separately. Evidence
version 8 records `initialSlideIn`, its source identity, direction, source start,
`attackTiming: authored-note` and `bendTiming: authored-tie`. Both package builder
and verifier require contract 67. The app's job identity also uses the new
contract, so an older conversion cannot satisfy a fresh request for this behavior.
Existing FeedPaks are not migrated; import again to obtain the corrected curves.

## Playback qualification and tests

Reference worker SHA-256:
`4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab`.
The full captured worker and extracted reference matched 584 checks in two
profiles. Isolating each of 21 real passages' own incoming cue left the native
finger-bend events and note timing unchanged in both profiles. Removing every
incoming cue in a part is not an equivalent control: a later incoming cue can
shorten the preceding synthesized note.

The committed synthetic fixture retains only finger-bend processor events, not
the incoming flourish. Native gesture clocks are normalized to source duration;
comparison allows one native position step plus two ticks and 0.023 semitones
for staircase/quantization error, including curve extrema inside that interval.
It covers both directions, electric/acoustic guitar, bass, score-start cues,
chords, repeats and tempo changes. This establishes scheduling compatibility,
not identical synthesized waveforms.

Additional tests cover precise positions, later bend controls, independent
vibrato, unsupported compounds, ordinary and Hybrid Lead packaging, linear and
piecewise alignment, source preservation and deliberate package mutations.
FeedBack rendering and scoring do not change in this fix.
