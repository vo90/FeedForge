# Verified recording-end cutoff

Preservation contract 56 extends the existing final-bar cutoff to recordings that
end several bars before the selected approved tab. It uses the same acoustic
evidence gate. It does not change or compress the earlier timing map, invent audio,
select another revision, or treat a saved success flag as proof of synchronization.

## Implementation

1. Validate the selected revision/video and a finite, strictly increasing source
   timing map. A recording ending inside that map may become a cutoff candidate.
2. Require the established recording-clock-v3 check to support the available
   recording before allowing omissions. Inconclusive evidence still stops import.
3. Omit individual attacks at or after the recording end; retain earlier attacks.
   Use the existing sustain rules for crossing tails. An unfinished pitch gesture,
   partly cut staggered chord, or completely removed arrangement still fails.
4. Preserve the original tab, the acoustic report, every omitted note and every
   permitted sustain shortening. The new receipt records the actual cutoff bar
   and mapped score end. Old short final-bar receipts retain their v1 format.
5. Independently reconstruct the source in package verification, recompute the
   cutoff boundary and acoustic result from packaged audio, and compare every
   retained note and omission. Preparation silence offsets both media and map;
   it does not move the evidence window within the original recording.
6. Run the same path for Hybrid Lead and use the existing import summary to show
   the number of omitted ending notes. No old FeedPaks are modified.

This supersedes the final-bar-only eligibility limits in the earlier recording-end,
sparse-sync and terminal-slide-cutoff documents. It does not relax their evidence
requirements. A musical-match assessment is not proof of every note's correctness.

## Validation

Synthetic recordings test a three-bar omitted suffix, an earlier held tail, late
chords, exact bar boundaries, malformed maps, preparation silence, and the full
worker with Hybrid Lead. Negative tests cover wrong timing, unauthorized candidates,
partial strums, old contracts, altered earlier attacks, missing notes, changed
cutoff indices and incomplete omission ledgers. Existing tests also substitute
silent audio and forge matching receipts to ensure the independent acoustic check
still rejects it.

The initial real-recording check of Working Man (approved revision 9197669,
original video iIGKlicb8n0) downloaded 430.834671 seconds successfully. The new
candidate accounts for 100 late note events, but timing was inconclusive in three
overlapping ending windows (400–416, 408–424 and 416–430.834671 seconds); 50 other
windows passed. No FeedPak was accepted and no thresholds were changed to force it
through. This outcome does not establish that the tab is wrong.
