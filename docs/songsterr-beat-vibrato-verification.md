# Beat-level vibrato warning verification

Contract 94 retains these source-bound warnings and written beat annotations
without creating note controllers. The earlier warning-only correction below
preserved the historical fallback; see
[note-owned vibrato](songsterr-note-vibrato-ownership.md) for its explicit policy
transition and historical archive verification.

The compatibility inventory retains active Songsterr beat `vibrato` and
`wideVibrato` markings as `display_or_expression` limitations. Their precise
playback interpretation is not established by the reference player. Existing
legacy vibrato conversion and written notation remain the converter's current
behavior; the warning makes that limitation visible.

The independent package checker omitted these two fields from its source-derived
warning inventory. It consequently rejected valid inventories as unexpected
findings. The retained Mr. Crowley source exposed 75 `beat.vibrato` and three
`beat.wideVibrato` findings with this mismatch.

## Change

Include both fields in the checker's independent traversal of selected guitar
and bass beats. Apply the existing inactive-value rule and verify each active
field by its exact source location, value, retention, category and impact.
Duplicates, missing warnings and warnings without a source marking remain
verification errors. The verifier does not import the producer's inventory to
construct expectations.

This is a general verifier correction, without song identifiers or exceptions.
It changes no source parsing, chart generation, note timing, vibrato rendering,
scoring or archive structure. Contract 63 remains current: the same previously
generated archive can now pass its intended source-preservation checks.

## Validation

Tests cover both markings, inactive values, multiple markings at one beat,
guitar and bass, rests, ties, chords, voices and repeats. Hand-authored warning
inventories exercise independent verification. Completed-package tests reject
altered pitch, attacks, sustains, lost/shifted vibrato intervals and missing
warnings. Hybrid Lead, generated difficulty, note-level vibrato and piecewise
recording-time mapping are covered together.

The retained complete Mr. Crowley test package fails only compatibility coverage
on the unchanged integration baseline and passes with the correction, with the
same source and archive hashes. That isolated check uses synthetic test audio;
packaged production audio/import acceptance is recorded separately.
