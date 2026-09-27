# Explicit Songsterr pickup bars

## Implementation and acceptance plan

1. Reproduce the public player's explicit first-bar rule: actual duration is the
   longest performed voice, separate from the surrounding time signature. Require
   boolean flags and agreement across parts; do not infer pickups from ordinary
   underfilled bars. Full-length flagged openings remain unchanged. Source and
   written rhythms remain intact. Confirm closing-bar semantics separately before
   changing them; a part flag alone does not shorten arbitrary final bars.
2. Update performed timing, beat grids and existing `pickup: true` notation. Use
   an independent implementation of source facts and checks, and bump the
   preservation/evidence contract so old output is not reused as currently verified.
3. Test short/full bars, rests and voices, invalid/conflicting flags/durations,
   grace/swing/tempo interactions, ties/repeats, negative recording boundaries,
   and tampered package timing/notation. Recheck all 16 flagged saved sources.
4. Commit on the feature branch, integrate into `integration/songsterr-test`, and
   rebuild FeedForge in the shared Songsterr runtime. Refresh TabView's small
   source-only display adapter there, retaining the existing game/native build. Preserve previous
   packages/logs and independently verify replacement imports and game playback,
   both highways and TabView. No separate game runtime per change.
5. Reassess Highway To Hell using the original recording and corrected source
   clock. Keep the existing synchronization gates, negative-time rejection and
   original-audio fallback. No new omission, audio pitch correction or scoring
   policy is part of this source-fidelity change.

## Source evidence

Investigation: `verification/songsterr-opening-investigation-20260927/REPORT.md`.
Songsterr's public `Oo` processing uses the maximum voice duration for its
explicit anacrusis opening. Its `xu` beat iterator places complete beat ticks
relative to the following downbeat, without accenting the pickup as a downbeat.
`wu` and `bk` use the resulting performed boundaries for recording interpolation.
The first-bar rule does not authorize edits to the recording's synchronization
points, omission of negative-time attacks, or guesses about missing audio.

## TabView integration found during acceptance

The legacy GP5 bridge grouped bars solely by metronome ticks. A fractional pickup
can have no tick at its first note. The import therefore retains
`import/pickup-timeline.json`, referenced by `song_import.pickupTimelineFile`:
source SHA-256, performed occurrence, original signature, quarter extent, and
recording-time/musical-quarter anchors. Independent rational verification checks
every field. This also covers arrangements whose pitched notation is unavailable.

TabView uses this display-only clock to retain short bars and opening notes,
restore alphaTab's anacrusis flag, and keep its cursor aligned. The highway,
scoring, metronome grid and original source remain authoritative and unchanged by
the display adapter. Full-length flagged bars do not receive a pickup receipt.

Sources: <https://www.songsterr.com/howtoreadtab#anacrusis> and
<https://static3.songsterr.com/production-main/static3/latest/common-CqwNT9PGZK_KaVsj.js>.
