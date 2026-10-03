# Tied staccato finger bends

Songsterr playback resolves ties before shortening the sounding attack for
staccato. Finger-bend timing is resolved afterwards: a following tied segment
with its own bend supplies the preceding bend's endpoint; otherwise the bend
uses its segment's resolved note-off. The initial segment's note-off includes
the entire tie before staccato, while hidden continuations retain their own
written duration/articulation. No continuation creates a second attack.

The converter applies this rule to tied Songsterr finger bends with staccato on
any segment. It retains segment identities until the tie is complete, then clips
the resulting curve to the original attack's sounding interval. Boundary values
are interpolated without accelerating a later target or inventing a release.
Tempo boundaries are inserted before mapping the curve to recording time.
Written notation and original source are unchanged. Ordinary tied and untied
bends are covered separately by [contract 59](songsterr-finger-bend-timing.md).

The source synth's one-tick MIDI event separation and quantized pitch values do
not become new musical events: the continuous chart uses exact authored values
and musical boundaries. Staccato's half-duration convention is source playback
behavior, not a universal rule for human performance.

`import/staccato-bends.json` records each interpreted chain, its source IDs,
written and gesture intervals, audible status and final curve in score seconds,
bound to the unchanged source hash. Preservation contract 31 is required. The
independent verifier reconstructs timing/evidence directly from its own source
atoms; missing/altered evidence or incorrect chart timing is rejected.

Tied-staccato slides, whammy gestures, displaced strum attacks and interleaved
overlapping bend controls remain explicit verification limitations. Their
scheduling is not inferred from the finger-bend rule. Broken ties remain errors.

Evidence: public Songsterr worker `FluidsynthAudioPlayerWorkerEntry-Do-Dwi4LNDxTupzE.js`,
captured 2026-09-23, SHA-256
`9bc2e262f42077e5f6d13d7c67269d8e92c2e24f8d6ff47ca9601b235251c33f`:
`ks` (ties), `Cs` (staccato), `xo` (finger bends), and `uo` (hidden continuations).
Known-answer tests cover independent synthetic examples, package tampering,
tempo/audio maps and the same semantic pattern as the frozen Raining Blood
measure 9 example. There are no song, artist, revision or fret exceptions.
