# Measure-boundary notices

Overfilled source bars can require the existing Songsterr playback boundary rules
to shorten a beat or skip a beat outside its written bar. The player notice must
describe only the adjustments that actually occurred. A shortened duration does
not imply an omitted attack, and an omitted beat may contain a rest or a tie
rather than a new attack.

## Implementation plan

1. Generate the converter's summary from the recorded adjustment actions, with
   separate wording for shortening, omission, and their combination.
2. Present complete, verified boundary findings as one warning, with counts and
   readable source bar, voice, and beat locations. Recognize both the old blanket
   summary and the new accurate summaries as duplicates only when the structured
   evidence supports that interpretation.
3. Keep unknown messages, incomplete reports, malformed findings, and unverified
   results visible for review. Preserve the original reports and logs.
4. Test shortening alone, omission alone, mixed adjustments, and evidence guards.
   Compare the Classical Gas report before and after; qualify the packaged
   converter and the notice shown by the shared app.

This is a reporting change. Preservation contract 122, playback policy, timing,
notes, durations, import acceptance, and existing FeedPaks remain unchanged.

## Implemented behavior and validation

The player summary counts distinct written beats, not notes, attacks, or repeat
visits. A duration cap followed by clipping counts once. A beat that is ultimately
omitted counts only as omitted. Full adjustment rows stay in the saved report.
Up to eight representative locations are displayed, matching the existing notice
detail limit.

Consolidation requires a completed, verified import and a complete report whose
boundary findings all match the recognized semantics and row structure. Matching
legacy or new summaries are quieted only as duplicates; unfamiliar wording,
incomplete evidence, and additional limitations remain visible. No finding is
downgraded from warning to information.

The producer/boundary regression selection passed 200 tests. The song-browser
suite passed 1,116 tests, with two existing environment-dependent skips. The
retained Classical Gas report has 13 clipped beats and no omissions; its new
summary removes the duplicate without changing other limitations or source data.
