# Combined holds and gradual tempo

Songsterr's public performer runs fermata expansion before gradual tempo
expansion (`Zr`/`Qr`, then `Vr`/`Kr`). A hold creates a temporary tempo and a
restoration event. The next linear destination interpolates from its immediate
predecessor in that expanded list, which may be a restoration event. A hold
replacing an authored destination also replaces its `linear` flag. Explicit
tempo events at the restoration boundary keep priority.

FeedForge now applies this order in both its converter and its independently
implemented source verifier. Existing rational interpolation, BPM rounding,
tempo-unit handling and repeat traversal remain unchanged. Original source
bytes, notes, written rhythm and tempo annotations are preserved. Existing
single-feature imports have the same clock, so the preservation contract does
not change. No game schema or playing/scoring rule changes are required.

Unknown or missing hold lengths, overlapping holds, mid-measure authored tempo
changes in a hold's measure and conflicting part clocks remain rejected. This
does not infer a hold duration or repair contradictory timing instructions.

The synthetic fixtures were replayed against unchanged functions from the
public worker captured on 2026-10-01:
https://static3.songsterr.com/production-main/static3/latest/FluidsynthAudioPlayerWorkerEntry-C-9Kd3shWgzk98Al.js
SHA-256: `4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab`.
They contain no downloaded song notes. Tests cover holds before/within ramps,
replaced destinations, explicit restoration boundaries, dotted and non-quarter
tempo units, meter changes, repeats, source preservation and archive mutations.
