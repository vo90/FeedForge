# Recording timing and combined ending tails

Preservation contract 37 adds a recording-clock fallback and a combined ending
policy. It does not alter source pitches, attacks, arrangement selection, scoring
or the recording's tuning. A supported clock is not a claim that every source
note matches the performance.

The existing conservative timing check remains the first path. If inconclusive,
the fallback measures pitch and attack at the same offset. A bounded audio-only
reference estimate can be used only when it improves support in both selection
and held-out, non-overlapping body passages. The ending is excluded from reference
selection. Source tuning annotations alone never authorize a reference change.

Sparse ending support requires at least three distinct supported times covering
the entrance and exit of a region of at most 32 seconds. Repeated indistinguishable
attacks, a final chord alone and suspected whole-passage timing mismatches cannot
authorize this path. Unassessed parts and events remain recorded in the evidence;
the converter preserves them rather than replacing their timing or pitches.

## Ending decisions

1. Retain events inside the original recording.
2. Prefer minimal silent padding when every attack is inside and all tails fit
   within two seconds. No missing music is synthesized.
3. When only eligible long held/muted tails exceed that limit, shorten those
   tails at the original recording endpoint and recompute minimal padding for
   the remaining short gestures. Require independently supported ending timing.
4. Decline the combined policy for a late attack or a long timed gesture. The
   separate existing late-attack cutoff policy keeps its original bounds.

The combined decision depends on actual event ends, not unused final-bar space.
It introduces no broader directional-slide cutoff permission. Its version-2
receipt distinguishes original recording duration, source event extent, trimmed
tails, prefix preparation and minimal suffix frames. Preparation shifts the
original cutoff once; appended silence never moves it.

The raw-source verifier reconstructs eligibility, compares the adjustment ledger,
remeasures the original audio region and checks decoded recording identity,
silence, frame counts, techniques and published chart content. Version-1 padding
and earlier recording-clock receipts remain independently verifiable using their
original acoustic rules.

Implementation and regression tests: `test_song_import_clock_evidence.py`,
`test_song_import_mixed_ending.py`, `test_song_import_ending_padding.py`,
`test_songsterr_recording_end.py`, and `songsterr-ui.test.cjs`.

Highway To Hell is an investigation case, never a source of special thresholds
or hardcoded tuning/timestamps. If its ending remains uncertain it must continue
to the existing audio fallback instead of being forced to pass.
