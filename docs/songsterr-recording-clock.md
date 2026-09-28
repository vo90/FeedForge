# Recording timing, phrase evidence and combined ending tails

Contract 39 adds a separate, bounded acceptance policy for an inconclusive
ending with a supported song body. See `songsterr-ending-warning.md`. It retains
the acoustic result and displays a warning; none of the acoustic rules below
are weakened or relabelled by that policy.

Preservation contract 38 adds `recording-clock-v4` with the bounded
`ending-phrases-v1` fallback for short ending padding. Existing version-2 and
version-3 evidence is replayed with its original rules. The separate policy that
omits attacks after the recording still uses version 3; this change grants no new
permission to omit notes.

If the existing dense and sparse checks are inconclusive, source-selected phrases
cover at most 32 seconds of ending plus eight seconds of preceding context.
Sixteen-second overlapping source intervals bound each comparison. At least
three ordinary attack groups spanning four seconds are needed. Pitch is sampled
inside short notes, and static held/tremolo pitches supply bounded context only.
Bass and guitar cues can also form one shared sequence; simultaneous cues and
duplicate arrangements never count as extra independent timing evidence.

Per-cue normalized pitch/attack evidence is combined geometrically at the same
candidate offset. The expected clock must beat offsets at least 200 ms away.
Earlier and later chronological halves must independently have audible evidence
and agree with that clock. A loud final chord cannot compensate for a wrong
earlier half. Two-second source-time bins keep rapid runs from outvoting sparse
later cues. Twenty bounded linear-drift hypotheses (endpoint offsets between
-0.8 and +0.8 seconds) must also score below the unchanged clock. These alternatives
are diagnostics only. No unrestricted time warping, source retiming or transcription
repair is performed.

Coverage uses actual measured phrase endpoints and actual source attacks. It
does not demand a cue before the first eligible event, or treat the entire span
of an overlapping successful window as verified. Uncovered source attacks,
inconsistent phrase offsets and insufficient ending support remain explicit.
The final supported cue must still fall within two seconds of the recording end.

The package verifier reconstructs notes from raw source, remeasures the original
part of the encoded recording, and checks the versioned phrase evidence. Scalar
feature metrics allow the existing small quantization tolerance; identity,
coverage, selected cues and decisions must agree. Padded silence supplies no
timing evidence. Regression fixtures include independently generated ambiguous
audio, partial shifts with the final hit unchanged, drift, duplicated phrases,
silence, gaps, interleaved instruments and a forged evidence receipt.

## Earlier recording clock and ending policy

Preservation contract 37 adds a recording-clock fallback and a combined ending
policy. It does not alter source pitches, attacks, arrangement selection, scoring
or the recording's tuning. A supported clock is not a claim that every source
note matches the performance.

The existing conservative timing check remains the first path. If inconclusive,
the fallback measures pitch and attack at the same offset. A clock match must
also beat competing offsets at least 200 ms away; a near-match to an equally
plausible repeated riff cannot authorize the fallback. A bounded audio-only
reference estimate can be used only when it improves body support and is
corroborated in both selection and held-out, non-overlapping body passages.
The ending is excluded from reference
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
or hardcoded tuning/timestamps. An uncertain ending can use the general contract
39 warning policy only when all its bounds are independently met; otherwise it
continues to the existing audio fallback.
