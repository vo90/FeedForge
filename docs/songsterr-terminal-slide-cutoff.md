# Directional slide-out cutoff (preservation contract 33)

## Implementation and acceptance plan

1. Extend final-bar candidate detection to overlapping targetless slide-outs.
   Require acoustic authorization even when there are no late attacks.
2. Cap only sustain and the crossing segment's end, retaining its direction,
   start and attack. Archive the exact change and independently reconstruct it
   from retained source during package verification.
3. Test both directions, tied chord members, boundary conditions, competing
   gestures, bad recording identity, insufficient evidence and forged reports.
   Replay Back In Black and the five accepted recording-end regression songs.
4. Merge the feature branch into the shared Songsterr integration branch, rebuild
   its existing FeedForge runtime and check the actual app import and all game
   arrangements, including the ending in both highways and TabView.

## Policy

`terminalSlides` declares `trim-final-directional-slide-v1`, bound to the actual
audio duration. It can be issued only after the existing `recordingEnd` acoustic
check supports the exact source revision/video map. The cutoff must lie in the
final performed bar, leave at most three seconds missing and use a bar no longer
than eight seconds. No independent timing fallback may bypass an inconclusive
acoustic check. Original audio and all earlier attack times remain unchanged.

Only explicit `slide_out_marks` in either direction qualify. The note attack and
marked segment start must already occur before the cut. Keep the attack, string,
fret, direction and segment start; cap the sustain and crossing segment end at
the recording boundary, rounded down to package precision. Do not move wholly
unheard slide segments earlier. Known-target slides, unfinished bends/whammy,
later incoming destinations or harmonic contacts still fail their existing
guards. A completed pitch movement's constant tail keeps its existing behavior.

The sustain-adjustments ledger has a `directionalSlides` policy and affected
note entries carry `slideOuts`: index, direction, start, originalEnd and exportedEnd.
The original source and written notation are retained. An empty ending-omission
ledger is allowed only when a current, source-derived directional cutoff requires
the acoustic check itself. Package verification independently rederives each
interval change, compares the ledger and playable data, checks actual audio
duration and reruns synchronization on the packaged recording.

The completion UI reports how many directional intervals finish with the audio
and where their original data is retained. Older contracts remain historical
evidence; they do not authorize this exception or current publication/reuse.

## Display

The existing targetless-slide visual convention places a short fade/taper in at
most the final 220 ms of the bounded segment. A capped segment therefore finishes
that visual flourish at the audio boundary. This explicitly changes the visual
endpoint; it does not invent a destination fret, pitch trajectory or new scoring
event. It is compatible with the current game and needs no game product change.
