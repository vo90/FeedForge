# Changing bends with terminal slide-outs — preservation contracts 63–64

A direction-only slide-out on a hidden tied continuation must not compress the
finger bend into the first written segment. For an otherwise isolated gesture,
resolve the bend against the completed tie, including tempo boundaries. Keep
the direction cue and its source interval independent of that pitch curve.

This extends the qualified `bend-hold-slide-out` case with
`bend-with-slide-out`. Both preserve one attack, its fret, duration and source
identities. Neither creates a target fret, release, new attack or synthesizer
pitch for an unspecified slide destination. The game's existing short terminal
flourish follows the bend height and retains its fade, taper and visibility
rules. No game or scoring change is required.

## Qualification boundary

The last segment must be a fretted, plain tied continuation with a direction-only
slide-out, no new bend, and the completed note end. Earlier slides, slide-ins,
whammy, attack offsets, HO/PO, trills, harmonics, mutes, scrapes and other existing
incompatible expressions remain guarded. Finger vibrato uses its separately
verified intervals; it is not baked into the bend curve.

Contract 64 also qualifies an otherwise valid bend when another attack shares
its voice and onset. Preserve the authored simultaneous chord clock. Native
automatic strumming introduces instrument-dependent synthesis offsets; those
are not authored attack times and are not imported. Paired native controls with
automatic strumming disabled qualify the completed-note bend independently of
the terminal direction cue. Explicit authored offsets still retain their guard.

If the next attack on the same string has a slide-in, retain the existing
limitation: it can change the endpoint and is outside this qualification.
Settled-bend behaviour is unchanged. Surrounding attacks are indexed once per
track, before generated trill attacks, outside any curve sampling or game frame
loop. No additional indexing or per-frame work is introduced by contract 64.

Unqualified cases retain their written-segment curve and compatibility finding.
They are not silently relabelled as exact playback matches. There are no song,
artist or revision special cases.

## Evidence and verification

The pinned public worker has SHA-256
`4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab`.
The checked-in synthetic fixture records its initial bend controller under both
authored and player-default instrument profiles. Paired controls remove only
the terminal slide; the initial bend schedule remains identical. Synthesized
slide note-ons and resets are deliberately excluded from the source attack and
fret contract. This is not an acoustic equivalence claim.

The native `xo` controller advances in source position steps and floors pitch
values. Its shared loop counter also advances past a control point before the
next ramp; releases can lag written interpolation by one such step. Real-source
reference checks bound that lag by one position step plus the synth onset tick,
with tone and MIDI quantization. The converter preserves the continuous authored
curve instead of importing these synthesis steps. Source-to-package pitch and
timing checks keep their existing strict tolerances.

Isolated changing bends require contract 63 and finger-bend evidence version
4. Their terminal evidence has `bendPhase: changing` and
`pitchPolicy: independent-source-bend`; `value` is the terminal bend value, not
a constant value across the slide interval. Newly qualified chord bends require
contract 64 and evidence version 5, with `attackTiming: authored-chord` on the
terminal evidence. Other packages retain evidence version 3 or 4 as applicable.
The independent verifier reconstructs the rule, context, curve and
evidence from the retained source, without using the producer's curve or helper.

Tests cover native samples, simultaneous chord membership, rise/release, both
directions, bass and guitar, precise controls, repeats, tempo changes, piecewise recording maps and package
mutations. Audio-end handling still rejects a cutoff through a changing bend;
authorized trimming of a settled tail remains supported. Hybrid Lead continues
to copy already verified note data.

Deployment and a fresh interactive import are separate from source verification.
Existing FeedPaks are not rewritten; fresh imports receive the corrected data.
