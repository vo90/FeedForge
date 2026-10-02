# Sparse synchronization evidence (preservation contract 32)

The final-bar eligibility limit was later extended by
[contract 56](songsterr-ending-suffix.md); the evidence rules below are unchanged.

## Implementation and acceptance plan

1. Preserve the normal recording-sync metrics and thresholds. Add a deterministic,
   bounded context check only for windows rejected for insufficient samples in
   every part. Keep the sparse passage's own acoustic evidence separate.
2. Version the acoustic assessment and conversion contract. Persist selected
   attack groups and both local/context metrics; independently rerun assessment
   from the source and packaged audio before publication.
3. Test synthetic delayed entries, internal sparse passages, competing parts,
   wrong prefixes, gaps, excluded gestures, offsets, drift, missing audio and
   unchanged source/ending safeguards. Replay the exact failed real song and
   previously accepted cutoff recordings.
4. Integrate the feature branch into the Songsterr integration branch. Rebuild
   FeedForge for the single shared runtime, import through the actual app, inspect
   the output independently, and check its arrangements in the game.

## Rules

The ordinary 16-second windows, eight-second stride, six-sample minimum and
pitch/onset thresholds remain unchanged. A window with enough material but an
inconclusive acoustic match cannot use the new path. The full-song check still
requires at least three active windows and support for every active window.

Only a part with two through five local picked/pitched attack groups can supply
sparse evidence. The local interval retains the usual 0.2-second end margin.
Every event in the complete window (including that margin) must have an assessable
fixed pitch and picked attack. Mixed chords with unassessable events, mutes,
legato, harmonic targets, scrapes, slide markers, moving-pitch effects,
nonintegral expected MIDI pitches and isolated single attacks remain inconclusive.

Starting with every attack group in that complete window, context grows toward
the closest adjacent source group until it has at least six groups. Earlier
groups win ties. No acoustic result influences selection; unsuccessful context
is not expanded repeatedly to hunt for a passing interval. Context must remain
in the same arrangement, inside the recording's sampleable region, within eight
seconds of either original window boundary and span at most sixteen seconds.
An adjacent gap over two seconds or an unassessable group cannot be crossed.

The context must satisfy all normal acoustic gates. Independently, each local
adjacent pair must satisfy those gates; an odd local count uses an overlapping
last pair. This stops added correct notes from diluting a bad sparse prefix.
At least two local groups are required, so no lone attack can grant permission
to remove ending notes. Groups in the original end margin are included in the
context, and are also normally assessed by the next overlapping window.

Every expected pitch also needs audible evidence at the onset-consistent sample
position (within 30 ms of the measured onset offset). A correct chord member
cannot conceal a missing member. Context pitches require the normal 0.10 minimum
score and 0.015 contrast individually; local and end-margin pitches also require
the 0.90 shift rank individually. The normal aggregate checks remain mandatory.
These extra sparse-path restrictions intentionally leave ambiguous observations
inconclusive even when the source could be musically correct.

Each report records the part, local interval, selected grouped attack timestamps
and expected MIDI pitches, local pair metrics, context metrics and decision
reason. These identify the mapped source groups; they do not claim isolated
instrument detection or verification of every note. This remains an engineering
check of the shared recording clock, not a calibrated probability of correctness.

## Preservation and versions

The algorithm never retimes notes, edits the source map, changes the recording or
expands the allowed cutoff. Existing final-bar limits, omission/sustain ledgers,
source identity and package checks remain mandatory. The assessment is
`mapped-pitch-onsets-v2`; new publication/reuse uses preservation contract 32.
Older reports remain readable as historical evidence. Their saved status cannot
stand in for the current independent verification.
