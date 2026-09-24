# Precise natural harmonic nodes (preservation contract 14)

Verified natural harmonics retain the authored integer `f` and add two
optional fields to the playable note: `hn` (touch position relative to fret
wires) and `hps` (the source's sounding semitone offset above the open string).
Both accompany `hm: true`; they also survive chord members, ties, generated
difficulty and the independent archive verification. The original source is
retained unchanged. This is an additive local extension, not an official
feedpak-spec version change.

For example `{f:3, hm:true, hn:3.2, hps:31}` preserves all three meanings.
The game shows its existing harmonic ring at 3.2 with a compact `3.2` label.
There is no "Touch" text. Physical position never becomes the MIDI pitch or
the note's identity. Equal-tempered notation uses open MIDI + capo + `hps`.

For example, a natural harmonic at fret 7 on low E retains the fret-7 harmonic
instruction and has MIDI pitch 59 (E2 + 19 semitones), not 47 (E2 + 7).
Source data remains byte-for-byte preserved. Missing specialized harmonic
engraving in an optional notation surface remains a display limitation;
the harmonic instruction and precise touch remain in the playable chart.

The node mapping agrees with both the public Songsterr worker's `Ui` table
and its common bundle harmonic pitch lookup, captured 2026-09-23. Worker hash:
`9bc2e262f42077e5f6d13d7c67269d8e92c2e24f8d6ff47ca9601b235251c33f`.
Common bundle hash:
`039a95156a0b966e7c3602ea8d0f4ae403266ec2f7ba930dd64fcbe41151fc29`.

The verified table additionally includes 2.4, 2.7, 3.2, 5.8, 8.2, 9.6, 14.7,
17, 21.7 and 24. Only floating-point noise within 1e-9 is normalized; unknown
nodes are not rounded. The source fret must agree with the node's tab shorthand.
Contract 16 adds the verified natural fret-15/node-15 alias to 14.7/34, recording
the interpretation while preserving the source. This applies to all matching
notes, not a specific song. See `songsterr-harmonic-support.md` for that alias and
the fretted harmonic extension. Unknown positions still are not rounded. A
target change inside a tie remains unsupported instead of rewriting the earlier
attack.

## Consumers and scoring

Deploy with FeedBack, the Note Detection plugin and Desktop's corresponding
`feat/precise-natural-harmonics` changes. Older readers can ignore these
optional fields and cannot promise a correct playing cue or pitch target.
The regular installed application is not upgraded by checking out this branch.

Scoring resolves known natural partials (2–8) to their physical frequency
ratio against the tuned/capo-adjusted open string, while keeping the stored
source MIDI approximation unchanged. This matters for the seventh partial,
which is roughly 31 cents below its nearest equal-tempered note. The ordinary
timing/pitch thresholds remain; there is no automatic hit or ungraded fallback.
Legacy `hm`-only packages retain their previous interpretation.

Tests cover independent source/package checking, mutation rejection, guitar,
bass, alternate tuning, capo, native and browser synthetic audio, and rendering.
Synthetic waveforms are not a substitute for final physical-instrument testing.
