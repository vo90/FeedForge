# Settled bend handoffs with note vibrato

Preservation contract 66 allows independently timed finger vibrato alongside a
settled bend handoff. Every earlier bend must have stopped changing before the
next bend controller starts, and its remaining terminal update must be at the
completed note end. This extends the existing general settled-handoff rule;
there are no title, artist, source-id or instrument-specific exceptions.

Modern `leftHandVibrato` and legacy note `vibrato`/`wideVibrato` use the existing
timed intervals. Their slight/wide instruction remains independent of the main
bend curve. Attacks, frets, sustain, source notation and vibrato intervals do not
change. The curve follows the authored tied-note clock, including tempo changes.
Synthesizer automatic strumming and endpoint tick rounding are not source attacks.

Conflicting bends, beat-only vibrato, slides, whammy, displaced attacks and other
unqualified compound expressions retain their prior interpretation and finding.
They are not made eligible simply because vibrato is supported elsewhere.

Eligible evidence carries `overlap.vibratoTiming: independent-note-controls` in
finger-bend evidence version 7. The independent verifier reconstructs eligibility,
curves and vibrato from source. Builder/verifier require contract 66 for this
combination. The application contract advances with it to invalidate stale jobs.
Existing imports are not rewritten; reimport produces the corrected data.

Tests cover captured-worker curves, modern and legacy vibrato, guitar/bass,
multiple controls, intensity changes, tempo/repeats, guarded combinations, Hybrid,
piecewise audio maps, and package mutations. Native reference samples allow its
integer position steps and pitch quantization; exact synthesizer waveform parity
is not a gameplay requirement. No new game technique or scoring target is added.
