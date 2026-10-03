# Open-string pickup positions

The shared generated hand-position policy is now `open-preparation-v1`.
After resolving fretted positions, a connected plain open pickup/run can use
the upcoming four-fret position when the destination is within 0.75 seconds.
This fixes Rats at 205.72s: its open bar uses frets 2–5 with the following fret-2
note, instead of inheriting frets 5–8 from the previous chord.

Written gaps over 1ms, active fretted sustains, mixed chords, techniques, wide
destinations and distant passages preserve their context. All musical fields
are unchanged. Both Python copies of `generated_hand_positions.py` must stay
byte-identical. Core's hash-guarded compatibility adapter upgrades old generated
positions in memory; no existing song archive needs to be rewritten.

The receipt recognizes `chord-local-v1` and the original absent position policy.
Only explicit regeneration upgrades those owned fields. Edited/authored and
unknown guidance remain protected. The independent coverage verifier accepts
both historical policies and the current one; import recipes use the current
policy automatically.

Validation: 111 guidance, pickup and import checks passed. A paired Core audit
covered 64 arrangements across 14 read-only song archives, with full active-fret
coverage and unchanged music/archive hashes. The production renderer checks
the resulting open bar's position and held-chord visibility.
