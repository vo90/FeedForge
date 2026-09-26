# Tied mute interpretation: implementation and test plan

Approved policy: a valid pitched Songsterr attack followed by a tied dead-note
flag keeps its original attack/target, full duration and pitch curves. Do not
invent another attack or an early cutoff. Preserve the conflicting flag with its
source position and report the unrepresented articulation explicitly.

1. Implement a narrow Songsterr-only tie rule. Ordinary dead attacks, initially
   muted ties, unpitched notes and scrape gestures retain their existing behavior.
2. Record located, repeated-occurrence-aware evidence in score time. Include it
   in compatibility reporting and a source-hashed FeedPak receipt. Require the
   new preservation contract for publishing this interpretation.
3. Reconstruct the interpretation independently from the source in verification;
   compare evidence, count, source hash and compatibility coverage, while retaining
   strict note/curve/target validation. Advance assessment/cache contract versions.
4. Test the original case and ordinary notes, initial mutes, cross-bar/repeated
   ties, mixed chords, scrapes, pitch curves and malformed ties. Mutate archive
   evidence and notes to prove that verification catches corruption.
5. Replay frozen guitar/bass sources and build a strict complete Killing in the
   Name archive. Distinguish source/package tests using synthetic audio from real
   app imports with original audio.
6. Integrate the independently reviewable fix into integration/songsterr-test,
   package FeedForge and refresh the existing shared runtime with preserved songs,
   profiles, logs and prior acceptance evidence. Reuse the unchanged game/native
   build. Run app import and game acceptance there. Do not create a per-fix runtime.

No new game symbol/scoring behavior, pedal reinterpretation or validator bypass.
No changes to the normal user library. No pushes. Unexpected musical ambiguities
remain reported blockers rather than being silently repaired.
