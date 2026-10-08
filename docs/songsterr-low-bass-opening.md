# Low-bass opening evidence (preservation contract 107)

Bounded opening repair previously excluded pitches below E1 (MIDI 28), including
the low B of a five-string bass. A valid low-bass opening could therefore lack
eligible evidence even though the source map and recording otherwise matched.

The normal opening path remains first. Only its lack of complete eligible source
groups permits the separate A0–C7 (MIDI 21–96) bass profile. An acoustic failure of
an eligible normal-range part cannot trigger a search for a better-sounding part.

## Eligibility and authority

- Bass parts only, with at least three low-pitch attack groups in the fitted
  opening and two in the held-out context. Low means MIDI 21–27.
- Every source attack group in those measures must remain eligible under the
  existing technique, pitch, and complete-group rules. No subset cherry-picking.
- Select one part using source group count and stable ID before scoring audio.
  A rejected part cannot be replaced by another part that happens to pass.
- Keep the existing negative-opening bounds, candidate grid, maximum density,
  audio-only tuning reference, per-group pitch/attack thresholds, onset-distance
  checks, and margin against competing timing candidates.
- Confirm the exact initially applied candidate after encoding, including the
  95%-of-best gate. Never replace it with a new winning candidate.
- Only the first two timing boundaries can change. Later boundaries, notes,
  pitches, techniques, and tuning retain their existing interpretation.

## Receipts and independent verification

`bounded-opening-low-bass-v1` requires contract 107. Its analysis profile records
the pitch range, bass instrument, fitted/held-out group counts, and low-group
counts. The receipt also retains selected part, exact candidate and locked
boundary, tuning reference, decoded-audio hash, and acoustic measurements.

Archive verification independently reconstructs source events, repeats normal
and low-bass eligibility and source-only selection, and confirms the stored
candidate against the archived recording. Profile/count/part changes, altered
coordinates or reference, mismatched audio, and contract downgrades fail.

Historical `bounded-opening-map-v1` and `bounded-opening-map-v2` receipts retain
their existing rules. Normal-range feature normalization is unchanged. The extra
low-bass feature pass runs only for an eligible fallback, during import and
verification, never during gameplay.

## Limits and tests

This measures recording timing; it is not exact pitch or octave verification.
Harmonic overlap can support timing in an octave-shifted recording, as with the
existing normal-range checker. It never transposes output notes or promises that
every authored pitch is correct. Insufficient or ambiguous evidence still fails
the bounded repair and follows the existing import fallback.

Tests cover real encoded 4/5/6-string imports, independent archive verification,
unchanged later anchors, wrong-pitch/delayed/missing-attack recordings, equal
competing openings, ineligible techniques, sparse and mixed-range parts, source
selection, profile limits, malformed/shifted candidates, old receipts, and forged
evidence. Controlled audio proves the capability; no named live song rescued
solely by this change has yet been established.
