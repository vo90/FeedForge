# Timed Songsterr finger vibrato — preservation contract 62

Keep one attack for a tied note, but preserve its finger-vibrato intervals and
slight/wide instructions in `vibrato_marks`. Times are seconds relative to the
attack; a present array is authoritative over the compatibility `vb` flag.
This is qualitative expression, never an exact oscillator or scoring target.

The completed-tie resolver follows the reviewed Songsterr controller schedule:

- The initial note's instruction extends to its completed tied end.
- Hidden continuations use their own intervals. A later reset stops the active
  instruction; it does not resume an older overlapping control.
- At a shared boundary a reset precedes activation. Adjacent equal intervals
  merge; differing strengths remain explicit. Sub-tick synthesizer resets are
  not represented as tiny gaps in a playing instruction.
- Modern note `leftHandVibrato` takes precedence over legacy note flags. Beat-only
  legacy flags retain their written intent with a compatibility limitation: the
  pinned player did not emit them.
- Direction-only slide-outs keep their existing source interval. Synthesized
  slide destinations, hidden note-ons and shortened synth-control release times
  are not invented as tablature. A settled bend followed by vibrato and a terminal
  slide-out can now use the verified bend clock. Changing/competing bends, beat-
  only vibrato and other unqualified compounds keep their existing limitations.

Interval endpoints pass through tempo changes, repeats, displaced attacks and
piecewise recording maps. Trill expansion intersects and rebases the intervals
onto its existing attacks. Authorized audio-end sustain trimming clips intervals;
it does not append silence or relax any pitch/slide-cutoff gate. Hybrid Lead copies
the already verified retimed events, including the extension.

Verification independently reads the retained source, reconstructs the controller
schedule in rational musical time, maps it to recording time and checks interval
presence, order, bounds, intensity and coordinates. It rejects removed, fabricated,
overlapping or shifted marks and downgraded preservation contracts. Original source
and written notation remain in the package. Ordinary bends stay in `bnv`; vibrato
is never baked into them.

Reference: pinned public playback worker SHA-256
`4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab`.
The synthetic fixture captures two qualified instrument profiles. Instrument
engines can use different controller families and waveforms; matching source
instructions is not a claim of acoustic or waveform equivalence.

Validation includes known-answer ties/precedence, both slide directions, native
control samples, tempo/repeat/chord cases, archive mutations, clipping, Hybrid
Lead and game wire/render regressions. Runtime activation and a fresh interactive
app import are a separate final check; this change does not rewrite old imports.
