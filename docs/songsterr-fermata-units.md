# Fermata tempo units

The public performer computes the fermata BPM in the authored tempo's unit,
rounds that BPM, then applies its note-value conversion. Rounding an already
normalized quarter BPM gives a different clock for half/eighth note tempos.
Generated hold/restoration events inherit `type`; they do not inherit the
original `dotted` flag. The importer follows this explicit playback behavior,
keeps original bytes and notation, and does not reinterpret an unspecified hold.

Evidence: `Qr`, `$r`, `ei` in public
`FluidsynthAudioPlayerWorkerEntry-Do-Dwi4LNDxTupzE.js`, captured 2026-09-23,
SHA-256 `9bc2e262f42077e5f6d13d7c67269d8e92c2e24f8d6ff47ca9601b235251c33f`.

Overlapping holds, mid-measure tempo/fermata combinations and combined enabled
ramps remain explicit interpretation guards. The supported non-quarter case
does not introduce a new playing technique or scoring policy.
