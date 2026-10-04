# Open-string pickup positions

Open-run preparation was introduced in `open-preparation-v2` and is retained by
[`slide-follow-v1`](slide-following-positions.md).
Resolve fretted positions first, then walk backward from each destination
through eligible plain open attacks. The whole run adopts the destination's
position and width, without a total-duration cutoff. This fixes Cirice at
212.3925–213.05375s: all three opens use frets 12–15 with the following fret-15
note. Rats at 205.72s continues to use frets 2–5 with the following fret-2 note.

Detached picks may have up to one local beat of silence between their effective
ends and the following attack (0.5 s without beats, clamped to 0.1–1 s with
beats). Longer gaps, active fretted sustains, mixed chords and techniques break
the run. A long rest before the first open does not prevent preparation. A
still-sounding open on another string may bridge shorter detached attacks.
Both full arrangements and generated difficulty levels use the same beat map.
All musical fields are unchanged. Both copies of `generated_hand_positions.py` must stay
byte-identical. Core's hash-guarded compatibility adapter upgrades old generated
positions in memory; no existing song archive needs to be rewritten.

The receipt recognizes `open-preparation-v1`, `chord-local-v1` and the original absent position policy.
Only explicit regeneration upgrades those owned fields. Edited/authored and
unknown guidance remain protected. The independent coverage verifier accepts
both historical policies and the current one; import recipes use the current
policy automatically.

Validation: 121 guidance, pickup and import checks passed. A paired Core audit
covered 64 arrangements across 14 read-only song archives, with full active-fret
coverage and unchanged music/archive hashes. The production renderer checks
the resulting open bar's position and held-chord visibility.
