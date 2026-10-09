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
- Let-ring stays a limitation: written and tied durations are preserved, but the
  game does not fully convey the instruction. This change does not resolve that
  separate capability gap.
- The app does not detect the target game version. Required consumer support is
  disclosed in import details, without claiming an installed version is supported.

`songsterr-notices.cjs` groups records and bounds representative details; complete
records remain exportable. New or unclassified free-text messages remain grouped
for review. Only explicitly recognized duplicates/routine messages are quieted.
The job manager caches the projection per job/evidence/assessment to avoid reading
large reports on every progress poll. Compatibility details prioritize gameplay
omissions and other warnings before the 200-record display limit. The saved report
and durable developer backlog keep original ordering, counts and resolution rules.

## Validation

Run `node --test test-song-browser/songsterr-notices.test.cjs
test-song-browser/songsterr-ui.test.cjs`, then the browser regression suite.
Coverage includes thousands of repeated diagnostic records, mixed supported and
omitted meanings, unknown and blocking findings, actual omissions, timing
uncertainty versus mismatch, lyrics/artwork, unavailable/modified evidence,
hash-checked report loading, cache invalidation and prioritized truncation.

Acceptance uses complete reports from the 40 completed overnight imports. Compare
raw diagnostics with grouped player notices and verify that source evidence bytes
remain unchanged. No new audio download is needed for this presentation change.
