# Terminal tied bend with slide-out — preservation contract 68

A final tied segment can carry its own bend controls and a direction-only
slide-out. Resolve the bend points on that segment's authored musical clock,
including tempo changes. Preserve the slide cue's source interval separately.
The attack, fret, completed tie duration and independently timed finger vibrato
remain unchanged. No slide destination, extra attack, release or scoring target
is invented. The existing game ribbon combines bend height with the short,
fading directional flourish and its normal visibility rules.

The preceding terminal-slide rules required this segment to have no bend
controls. Contract 68 extends that qualification to a terminal segment with
controls. It still requires a fretted tied continuation ending at the completed
note end, with no other slide inside the chain, incoming cue, whammy, authored
attack offset, unresolved overlapping bend controllers, harmonic expression,
beat-level vibrato, mute, scrape, trill, HO/PO or other existing incompatible
expression. Supported note-level finger vibrato remains independent. Chord
attacks and following slide-ins use the existing authored-clock policies.

An overlapping controller remains guarded even when a no-slide diagnostic
control would have a settled handoff. This composition has not been qualified
by this change. There are no song, artist or revision exceptions.

## Source-player comparison

The reviewed public worker has SHA-256
`4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab`.
Its `Ss` slide synthesis precedes `xo` finger-bend generation. Depending on the
instrument, generated slide sounds can shorten the terminal synth note and
therefore compress that bend's playback interval. Generated synth attacks,
guessed slide destinations and instrument-specific truncation are not authored
gameplay instructions. Preserve the source clock, as in contracts 63–67.

The synthetic reference fixture captures the finger-bend processor in both
authored and player-default profiles, qualified against the complete public
worker. Native controller samples are normalized to their source gesture
position before mapping through tempo changes. Comparison allows one native
position step plus two ticks and 0.023 semitones of source tone/MIDI quantization.
This does not relax the independent source-to-package verification tolerances,
and is not a claim of acoustic equivalence to synthesized slides.

## Evidence and verification

Newly qualified terminal segments record
`terminalSlideOut.bendTiming: authored-segment`. They require preservation
contract 68 and finger-bend evidence version 9. Other bend combinations retain
their existing evidence version. The independent rational verifier reconstructs
the eligibility, control timeline, evidence and output from the source without
using the producer's curve or helper. Old jobs cannot claim the new guarantee.

Tests cover holds, rises, releases, both directions, precise coordinates, very
short terminal segments, near-edge frets, guitar/bass, repeats, chords, following
attacks, tempo changes and piecewise recording alignment. Package mutations must
reject altered bend curves, cues, attack, sustain, fret, vibrato, evidence and
stale contracts. Hybrid Lead copies the verified data. Existing FeedPaks are not
rewritten; fresh imports receive this qualification.
