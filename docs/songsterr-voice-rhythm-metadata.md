# Obsolete voice rhythm metadata

Songsterr's optional `voice.hasSameRhythm` boolean describes deprecated rhythm
notation. It does not define playback, require equal rhythms, or identify
duplicate notes. Import every voice's explicit notes, rests, ties and durations
using the existing voice projection rules.

## Implementation

1. Validate a present value as a literal JSON boolean, before inactive-value,
   resting-voice and instrument-selection shortcuts. Missing is allowed; null,
   numbers, strings, arrays and objects are not qualified.
2. Preserve the original source and account for each flag as `source_metadata`
   with `source_retained` impact. It creates no player limitation.
3. Validate independently in the source/archive verifier, including excluded
   voices. Check exact typed values, locations, retention and unchanged music.
4. Use preservation/inventory contract 120 for new retention claims. Existing
   older archives containing false with no finding remain verifiable: that
   value was already accepted as inactive. True requires the new contract.

No score mutation, rhythm-equality heuristic, voice merging or musical
instruction is inferred from this field. Unknown active voice fields still
block import.

## Qualification and regression coverage

The reviewed Songsterr schema calls the field an optional Boolean deprecated
after rhythm notation moved to a single rendering path for all voices. The
retained common-script SHA256 is
`50bb5cd2fefe0bc4ea02cc5af0cbec205571e4df56031f09d92975be6de87d85`.
The reviewed worker SHA256 is
`4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab`.
Whole-part comparisons with original, absent, true and false flags in both
authored and player-default profiles produced unchanged note clocks,
traversal, synthesis events and automation (8/8 comparisons).

Tests cover one/multiple/resting/excluded voices, unequal rhythms and beat
counts, independent notes/ties, malformed inactive values, source retention,
archive tampering, older contracts, and Hybrid Lead with affine/mapped timing.

## Known separate blocker

Classical Gas revision 240775 contains 18 true flags, including six bars whose
voices have different explicit rhythms. The fix retains all 18. The original
source then still fails on two `beat.tremoloBar: true` values in bar 88.
This change does not qualify that separate structure, invent a pitch curve or
claim that the complete song imports successfully. Batch acceptance remains
paused pending that investigation.
