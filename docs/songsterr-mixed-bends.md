# Mixed Songsterr bend coordinates

Some approved scores include `precisePosition` on only some points of an ordinary
finger bend. This is valid source data, rather than an ambiguous musical effect.

Songsterr selects one timing mode for the entire curve:

- Without any precise point, use `position / 60`.
- With any precise point, use `precisePosition / 100` where supplied. Convert
  other points to `Math.round(position * 100 / 60) / 100`.
- At equal normalized positions, retain the last authored point.

The producer and independent source verifier implement this rule separately.
They retain existing validation of numeric values, order, bounds and bend pitch.
They do not sort, clamp or repair invalid curves. The original source remains
unchanged in the FeedPak. Whammy-bar handling is outside this change.

## Source evidence

Checked against Songsterr's public audio worker on 2026-10-01:

`https://static3.songsterr.com/production-main/static3/latest/FluidsynthAudioPlayerWorkerEntry-Do-Dwi4LNDxTupzE.js`

SHA-256: `9bc2e262f42077e5f6d13d7c67269d8e92c2e24f8d6ff47ca9601b235251c33f`.

Its `xo` finger-bend handler selects precise mode with
`points.some(e => e.precisePosition !== void 0)`, then uses the `En`/`Dn`
helpers for the whole curve. The legacy-coordinate conversion is rounded to
integer percent, not used directly as a fraction of 60 in mixed mode.

## Validation and compatibility

`tests/test_songsterr_mixed_bends.py` covers known-answer curves, rounding,
duplicate coordinates, malformed inputs, chords, ties, repeats, tempo changes
and independent archive verification rejecting altered timing or pitch.

Existing legacy-only and precise-only bends keep their previous semantics.
There are no new FeedPak fields or gameplay requirements: these curves use the
existing bend representation. Previously rejected mixed inputs become readable;
the preservation contract and game code do not change.
