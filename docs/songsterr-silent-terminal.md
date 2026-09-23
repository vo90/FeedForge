# Explicit silent terminal boundaries

Contract 10 additionally permits recorded final-sustain adjustments under the
narrow policy in `songsterr-terminal-sustains.md`. The rules below describe the
original silent-grid exception.

A source timing map may end the final written bar after the recording ends.
This does not invalidate an otherwise complete recording if only silent notation
extends beyond it. The supplied map is retained unchanged; no note, attack,
sustain, or bend may exceed the existing recording bounds.

Both explicit and source-rule inferred trailing boundaries follow the same
checks: the map must cover every performed measure and every playable event must
fit the recording. Interior missing entries and notes beyond the recording
remain blocking.

The public `video/putPointsIntoPlayer` handler repeats the last supplied interval
until there are progression-length + 1 boundaries, provided at least two points
exist. The converter follows this exact trailing rule and records the number of
inferred boundaries. It never fills null interior points or changes known points.
Evidence: public `common-z7xLi0BiPF1hudTP.js`, captured 2026-09-23, SHA-256
`039a95156a0b966e7c3602ea8d0f4ae403266ec2f7ba930dd64fcbe41151fc29`.

This removes arbitrary distinctions between explicit and inferred silent tails
without relaxing the independent audio matcher's confidence thresholds. A
source-provided timing map does not imply that its author matched the recording
perfectly; the conversion preserves the player's timing convention.
