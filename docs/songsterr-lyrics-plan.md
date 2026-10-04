# Songsterr lyrics import implementation plan

## Scope

Export Songsterr-authored lyrics through the Song Browser importer into the existing
FeedPak lyrics format. The game loader, renderer, preview behavior, highlighting,
and playback clock remain unchanged. No external lyrics lookup or transcription in
this first version. Work starts from Songsterr integration commit `4244482` in the
isolated `feat/songsterr-lyrics` worktree.

## Implementation

1. Inspect Songsterr's current public lyric interpretation and retained real scores.
   Qualify syllable tokenization, starting measure, rests, ties, explicit skips,
   repeated measures, and selection of one primary vocal stream. Preserve original
   source data and report unsupported or ambiguous lyrics without inventing text.
2. Capture revision-specific legacy lyric data when Songsterr advertises it, using
   existing bounded acquisition and revision identity rules. Keep missing lyrics
   optional; do not make otherwise playable instrument imports fail.
3. Convert lyric positions through the existing performed score clock and final
   recording alignment. Map both endpoints; handle leading/ending clipping
   explicitly. Export sorted, finite `{t, d, w}` events, with FeedPak `-` syllable
   joins and `+` phrase endings. Bound phrases for the existing renderer when the
   source has no useful line breaks.
4. Write `lyrics.json`, manifest provenance, and a retained conversion report.
   Include lyric policy/version in conversion evidence and validate the final
   archive against retained source. Songs without usable lyrics remain importable
   and do not claim verified lyric timing.
5. Verify synthetic edge cases, source semantics against Songsterr's public
   implementation, and retained real songs. Load finished output using the current
   game loader and exercise the unchanged renderer's grouping/timing behavior,
   including seek/loop/rate clock positions. Run relevant importer/acquisition
   regressions and the broader converter suite.

## Acceptance criteria

- No game source or renderer changes.
- Authored lyric timing uses exactly the imported recording's alignment.
- Main/backing streams are not silently interleaved; selection is recorded.
- No lyrics, malformed lyrics, and unsupported lyric notation have explicit outcomes.
- Final FeedPak events and phrase markers work with the existing game reader/display.
- Evidence distinguishes faithful conversion from acoustic accuracy of the source tab.
- Existing user FeedPaks and the running paired runtime are not overwritten during
  development; validation artifacts are kept in an isolated verification folder.

## Progress

- Plan created; implementation and validation in progress.
