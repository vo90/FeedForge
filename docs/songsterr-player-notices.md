# Songsterr player notices

Player notices are a presentation projection of the full, hash-checked import
evidence. They do not change the converter, preservation contract (109), import
acceptance, notes, durations, audio, scoring or the developer compatibility backlog.
Historical FeedPaks and their reports are not rewritten.

## Policy

- A known missing playing instruction, omitted notes, uncertain bend behavior,
  pitch mismatch, missing artwork/lyrics or limited Hybrid Lead coverage stays
  visible once per issue. Representative arrangement/bar locations are expandable.
- Routine key-signature retention, excluded instruments and timing provenance
  belong in diagnostics. Staff-notation limitations are informational in the
  guitar/bass tab import workflow.
- Staccato duration handling, chord labels and supported expression policies are
  informational only for their exact qualified converter descriptions. A changed
  description, unknown field, blocking finding or conversion-check finding stays
  visible for review. A feature name alone is not evidence of full support.
- Inconclusive acoustic checks are informational, with their passage counts and
  caveat retained. Suspected mismatches and explicit accepted-with-warning timing
  adjustments remain warnings. A timing pass is never described as proving every
  note correct. Missing/modified verification cannot become a success claim.
- Contract 110 retains `lr` and adds optional `let_ring_sus` in recording seconds.
  Written rhythm and scored `sus` stay unchanged. The updated highway uses the
  longer span only in its visual view, and tabview exports the let-ring marking.
  Qualified runs close at the last nonempty beat, are clipped by a same-string
  reattack, recording end and Hybrid Lead handover. Native pitch gestures keep
  their own duration policy; staccato halves the ringing span in the source clock.
  Muted notes do not get pitched ringing tails. Fresh imports report this as
  information; older missing-display reports remain warnings. Existing packages
  are not rewritten and need reimporting to acquire source-derived spans.
- The app does not detect the target game version. Required consumer support is
  disclosed in import details, without claiming an installed version is supported.
- Modern beat-level `vibrato` and `wideVibrato` annotations are informational
  only for the exact note-owned policy descriptions, after a completed import
  passes output verification and its hash-checked full verification includes
  `note_owned_finger_vibrato`. The compatibility report must be complete.
  Explicit note vibrato keeps its existing playback; a beat annotation does not
  add vibrato to unmarked notes. Original descriptions and locations remain
  available. Historical wording, unfamiliar meanings, failed or modified output,
  and unavailable or incomplete evidence remain warnings. This presentation
  change keeps preservation contract 122 and does not rewrite any FeedPak.

`songsterr-notices.cjs` groups records and bounds representative details; complete
records remain exportable. New or unclassified free-text messages remain grouped
for review. Only explicitly recognized duplicates/routine messages are quieted.
The job manager caches the projection per job/evidence/assessment to avoid reading
large reports on every progress poll. Compatibility details prioritize gameplay
omissions and other warnings before the 200-record display limit. The saved report
and durable developer backlog keep original ordering, counts and resolution rules.

## Validation

Contract 113 allows optional Hybrid Lead subphrases with two active quarter-note
beats and two distinct pitched attacks (previously four beats). These still fit
inside the existing guarded lead rests and retain complete written slots and
connected gestures. Primary selection, solo priority, tuning/capo eligibility,
and search budgets are unchanged. Older contracts retain their four-beat rule.

Hybrid notices distinguish overlapping parts, unsupported gestures, incomplete
searches, excluded sources and unresolved lead passages. Verified high-fret
omissions also explain their Hybrid Lead impact in the existing omission notice.
Unknown reasons and limited reports without an explanation remain warnings.
For new imports, limitation details include recording-time ranges of omitted
obligation events, excluding already selected events. The source arrangements
and complete source evidence remain available.

Run `node --test test-song-browser/songsterr-notices.test.cjs
test-song-browser/songsterr-ui.test.cjs`, then the browser regression suite.
Coverage includes thousands of repeated diagnostic records, mixed supported and
omitted meanings, unknown and blocking findings, actual omissions, timing
uncertainty versus mismatch, lyrics/artwork, unavailable/modified evidence,
hash-checked report loading, cache invalidation and prioritized truncation.

Acceptance uses complete reports from the 40 completed overnight imports. Compare
raw diagnostics with grouped player notices and verify that source evidence bytes
remain unchanged. No new audio download is needed for this presentation change.
