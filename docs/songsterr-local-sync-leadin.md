# Local recording synchronization and preparation time

## Implementation plan

Work on `feat/songsterr-local-sync-leadin`, based on the combined Songsterr
integration. Use the existing shared runtime after integration; preserve all
source recordings, previous imports and investigation evidence.

1. Add a bounded, versioned acoustic timing assessment. Compare pitch and attack
   at the same candidate offset, account for independently estimated tuning,
   and report assessed/uncertain passages without claiming every note is verified.
   Ordinary source-map imports retain their existing acceptance policy. A failed
   acoustic measurement is not proof of a faulty tab.
2. Add an opening-only repair for exact-revision/exact-recording maps rejected
   for negative playable attacks. Keep written notes and rhythm intact. Fit only
   the first two recording boundaries, lock the next and all later boundaries,
   and require distinctive onset/pitch support plus unused neighbouring evidence.
   Reject ambiguous, missing, wrong-pitch or structurally incompatible openings.
   No title, artist, song ID or hand-entered timestamp may select a repair.
3. Preserve the original map and correction receipt. Independently reconstruct
   the score for repair verification and rerun acoustic evidence against packaged
   audio. A correction cannot authorize ending omissions or waive cutoff gates.
4. After synchronization, ensure at least two seconds before the earliest playable
   event in any exported arrangement. Add only the missing preparation duration,
   in whole audio frames. Shift all absolute chart/display timestamps equally;
   relative technique timings and written rhythm stay unchanged. Retain the
   original recording clock for evidence. Keep preview audio musical and avoid
   redundant encoding where feasible. Measure actual processing overhead.
5. Expose bounded timing findings in import results/evidence and preserve fallback
   behavior. Successful source conversion, acoustic timing support, and uncertain
   passages must remain distinguishable.

## Test and acceptance plan

- Synthetic known alignment, detuning, repeated riffs, silence, wrong pitches,
  shifted/drifting clocks, missing opening attacks, pickup bars and held-out notes.
- Mutation tests for invented repairs, changed later anchors, corrupted receipts,
  omitted notes, shifted only-one-arrangement and incorrect padded audio/duration.
- Preparation tests with immediate/delayed entries, chords, bends, sustain cuts,
  notation, pickup display clocks, beats, sections, previews and multiple parts.
- Replay retained Highway To Hell and available previous corpus evidence; do not
  redownload media unnecessarily. Audit the available 100-song cohort and report
  coverage honestly, distinguishing replay from a new live full-corpus run.
- Run converter and desktop contract suites, then integrate and rebuild FeedForge
  in the one shared Songsterr runtime. Check imports and chart/TabView timing in
  the game. No physical instrument testing is claimed.
- Highway's unresolved outro remains a separate gate. Do not force publication
  or relax the previously approved recording-end safeguards to reach a count.

## Status

Implementation uses preservation contract 35. Production and independent
verification both reconstruct their own source events before acoustic checking.
The original Songsterr map is retained separately from an opening correction and
from preparation time. The acoustic report's `audioSha256` hashes decoded float32
samples (with rate/channel/frame identity), avoiding incidental WAV PEAK timestamps.

The diagnostic reference is estimated from audio alone. A low-concentration
estimate remains explicitly inconclusive; it is a bounded analysis hypothesis,
not an assertion of player tuning or permission to transpose/retune. Correction
still needs distinct pitch/attack evidence and unused neighbouring attacks.
Mixed pitched/dead-string chords contribute their fixed pitches only. This checks
the shared recording clock and never claims verification of every string.

An ordinary structurally valid source map retains its previous publication policy.
Acoustic uncertainty and suspected offsets appear in the saved report and app;
they do not silently rewrite the source. Opening repair is currently limited to
negative playable starts within 0.75 seconds, two adjustable opening boundaries
and a locked third boundary within eight seconds. Broader local/global retiming
is intentionally not inferred from ambiguous diagnostics. Recording-end omission
and directional-tail checks keep their separate preexisting authority.

For preparation, the worker stages lossless analysis audio, then performs one
full-track Vorbis encode from the original decoder source with the required
number of zero frames. The preview is generated from the original music. All
absolute output coordinates derive from the shifted map, including pickup clocks
and notation. Relative sustains/techniques retain their original mapped durations.
Negative silent score bars can become visible during the new preparation period;
their retained downbeats are numbered consecutively and independently verified.

Recording-end acoustic windows stay in the original recording coordinate system,
so adding preparation cannot move a previously checked passage into another
window. Their evidence is recomputed against the actual final encoded audio.
Independent diagnostic score comparisons allow at most 0.005 absolute / 1%
relative numerical differences at FFT frame boundaries. Decisions, offsets,
coverage, recording identity and the microsecond chart checks remain exact under
their existing contracts. Diagnostic metric tolerance cannot authorize a cutoff.

Source tests: 1,769 passed, five existing skips; desktop/JavaScript tests: 771
passed, two existing skips. The final UI subset adds a preparation/repair/evidence
rendering check (19 passed). The shared-runtime and retained-corpus results are
recorded in the workspace verification report after acceptance.
