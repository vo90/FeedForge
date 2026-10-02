# Songsterr trills

The Songsterr importer expands a pitched trill into ordinary FeedPak notes.
The starting attack retains its authored articulation. Each later higher fret
is a hammer-on, each lower fret a pull-off. Ties extend the gesture before
expansion; they do not restart it. An authored outgoing legato uses the final
sounding fret. No game timing tolerance or scoring policy changes.

The source player's duration-cap/trill stages and adaptive per-part clock define the interval:
cap speed by the written beat duration, quantize `tpqn * speed / 480` to a
whole tick, leave gestures shorter than 1.5 intervals as one note, otherwise
alternate for `max(2, floor((durationTicks + 1) / intervalTicks))` notes. The
last note owns the remaining duration. Musical positions stay rational until
tempo and recording alignment map each endpoint. Source synth clock rounding
is local to the trill interval; the importer does not quantize the rest of
the score or copy synth-specific note-off gaps.

Supported inputs include higher/lower auxiliary frets, open strings, tied
duration, staccato, repeats, tempo changes, swing and simultaneous trills.
Malformed values, same-pitch ambiguous exits, or
unverified combinations such as a harmonic/bend/slide/whammy/tap on a trill
remain diagnosed. Nothing is silently discarded or turned into another
technique. Expansion has the existing 500,000 performed-note limit.

Written notation stays separate: generated notes are not invented written
beats. The original source is retained byte-for-byte. `import/trills.json`
records source hash, policy version, source IDs, performed occurrence, clock,
rate, frets, exact quarter-note endpoints, stable event IDs/ordinals and
articulation/link decisions. The manifest points to this receipt with
`song_import.trillsFile`. These are import evidence, not new required game
note fields. Preservation/verification contract 23 prevents old results from
being reused as newly verified conversions.

Contract 49 adds marked tied continuations when the native events form one
continuous, unambiguous pitched sequence. The initial marked note expands over
its folded tie duration; a marked continuation emits its local alternations
without another initial attack. Commands at the same time retain native source
order. Redundant note-offs are harmless; overlapping pitches, repeated-pitch
reattacks, internal silence and tied staccato remain diagnosed. We neither
choose a sounding pitch nor remove an attack to make such cases pass.

This path uses the captured worker's one-tick-early release only for its
short-gesture threshold, then restores the authored musical endpoint. It does
not add synthesis release gaps to the game. Each surviving pitch change uses
the existing HO/PO representation. Ordinary trills retain their previous output.
The version 2 receipt (`songsterr-trill-hopo-v2`) additionally identifies every
marked segment and its clock/rate, including marks on hidden tied notes.

The reviewed reference is pinned in the compatibility manifest. Its current
trill stage is `Is` (previous captures named it `Fs`); symbols alone are not a
stable reference identity. `tied-trill-reference.cjs` generates 256 synthetic
combinations from that captured worker. Full-worker qualification, independent
source evaluation and archive mutation tests cover accepted sequences and
the ambiguous combinations that must remain blocked. No song identity or
measure number controls this rule.

`verify_trills.py` independently reads raw source and rebuilds the sequence;
it does not call the production parser/expander. Final package verification
checks every note, chord and retained receipt. Tests cover nonlinear audio
mapping and deliberate pitch, timing, sustain, technique, link, count,
source-identity and evidence mutations.
