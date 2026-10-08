# Encoded opening confirmation (preservation contract 106)

The bounded opening repair searches for a supported replacement for the first
two video-map boundaries. Previously preparation repeated that search after
audio encoding and required exactly the same winning grid point. Encoding can
move the winner by one 10 ms grid step even when the applied candidate still
passes every acoustic check.

Preparation now confirms the original candidate against the encoded recording.
It does not change the candidate or search another arrangement until one passes.
The independent archive verifier reconstructs the source notes and confirms that
same candidate from the finished recording.

## Rules

- The initial proposal still uses the existing bounded search and source-only
  arrangement selection. Its negative first boundary must be within 750 ms of
  recording start. Only the first two boundaries may change.
- Confirmation accepts exactly one published, six-decimal grid candidate from
  that same search space. Arbitrary nearby coordinates are not accepted.
- Every fitted and held-out note group needs the existing pitch support (0.12),
  attack support (1.3), and onset proximity (55 ms).
- The candidate must remain 1.15 times stronger than materially separated
  alternatives and score at least 95% of the best encoded grid candidate. This
  is a relative acoustic score, not a probability or a relaxed timing window.
- The third boundary and all subsequent boundaries remain unchanged, apart from
  the existing preparation-silence offset applied to the complete recording.
- No note, technique, tuning, or pitch is changed by this confirmation step.

## Evidence and compatibility

New confirmations use `bounded-opening-map-v2`, requiring preservation contract
106 in the builder, source-map verification, and independent acoustic verifier.
The receipt adds fitted/held-out attack scores and `candidateToBestRatio` to the
existing source identity, coordinates, pitch scores, onset error, tuning, and
decoded-audio hash. The encoded winner coordinates are deliberately not part of
the decision or receipt.

Historical `bounded-opening-map-v1` receipts retain their original re-fit and
comparison rules. Compatibility inventories from contract 105 remain accepted.
The one-unit rounding tolerance applies only to acoustic diagnostic scores;
timing coordinates, decisions, tuning, and audio identity stay exact.

## Regression coverage

- Two deterministic real-audio worker imports where encoding changes the winning
  grid point; original candidate retained and finished archive independently
  verified, including unchanged later anchors.
- Wrong pitch, absent fitted/held-out attacks, competing openings, invalid
  candidates, and a candidate passing old gates but failing the new 95% gate.
- Source-only part selection, bounded metric rounding, forged receipts, changed
  audio hashes/anchors, and contract downgrade rejection.
- Historical contract-105/v1 worker output and independent verification.
- Existing preparation, source timing, ending, evidence, and app regressions.
