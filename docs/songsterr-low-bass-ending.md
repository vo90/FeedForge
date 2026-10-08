# Low-bass ending evidence (preservation contract 105)

The legacy recording-clock analysis starts at MIDI 28 (E1). Valid lower bass
attacks could therefore supply no timing evidence and block an otherwise valid
recording-end cutoff or short padding operation.

The ending verifier now tries a separate MIDI 21–96 feature bank when legacy
evidence is inconclusive. It does not change the legacy bank or normalize its
features differently. Already supported windows and suspected mismatches are
retained. Reports which do not use the fallback retain their legacy version and
schema.

## Authority

- Only bass parts with at least six eligible low-pitch attack groups in the
  existing window qualify. Each selected group contains a MIDI 21–27 pitch.
- Muted, legato and moving-pitch gestures remain ineligible. Higher-only groups
  cannot supply evidence on behalf of low notes.
- Pitch and attack evidence are measured at the same clock offset and existing
  independently selected tuning reference. No new reference search is granted.
- Existing joint thresholds apply, including a distinct near-clock peak versus
  competing offsets at least 200 ms away. Sparse/phrase fallback is not extended.
- Only inconclusive windows can gain support. Normal recording-end bounds,
  recording identity and preparation rules still apply.
- Notes, pitches, tuning, map points and the final diagnostic policy are not
  changed. This is shared-clock evidence, never proof that every note is correct.

`recording-clock-v3-low-bass-v1` extends cutoff evidence and
`recording-clock-v4-low-bass-v1` extends padding evidence. Both require contract
105. Their `lowBassEvidence` retains range, reference, windows, participating
parts, low-group counts and measurement outcomes. Independent archive
verification reconstructs source events, decodes the original recording region
and compares the complete recomputed evidence, in addition to receipt hashes.
Legacy v2/v3/v4 receipts continue using their original rules.

## Tests and limits

The tests cover A0/B0/C1/D1 ranges, four/five/six-string imports, real encoded
audio, cutoff/padding/combined endings, delayed/wrong-pitch/noise controls,
repeated patterns, sparse/excluded material, receipt tampering and contract
downgrades. Established ordinary-range reports are compared unchanged.

The fallback cannot establish every pitch or octave, and ambiguous recordings
remain inconclusive. Opening repair and its separate encoded 10 ms grid issue
are outside this change. Final diagnostics still use their existing pitch range,
so a completed low-bass import may retain a timing-not-fully-verified warning.
