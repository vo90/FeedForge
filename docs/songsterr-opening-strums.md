# Authored opening strums

An explicit Songsterr brush/arpeggio can place individual strings before the
first written beat. Score time below zero does not imply missing recording
audio: the first synchronization point often occurs after the recording starts.

The converter retains those rational offsets and continues the initial tempo
before score zero. Unsupported opening grace and arbitrary negative events
remain rejected. Written beats, later tempo changes, repeats and note endpoints
are unchanged.

For a recording-specific source map, `provenance.openingStrum` records the source
groups, negative member count, and earliest score time. Only those authored
members may extend the first interpolation interval. The original boundary
array is retained unchanged; there is no general permission to extrapolate.
An attack before the original recording still triggers the existing alignment
fallback. Preparation silence never supplies evidence for missing audio.

The independent evaluator reads original note/strum fields, applies its own
clock, and verifies the receipt, including separated or combined voices. It
also checks original recording coverage after removing the preparation offset. Ordinary
archive checks verify attacks, durations, notation, display groups and retained
source. The existing preparation stage adds only enough silence for the first
playable attack to occur at two seconds and shifts the complete export together.

Reference: the public player interpolation `KO` captured 2026-10-01 (common asset
SHA-256 `8b9267cd39f7f3b0511bade44de01cf3fe7c8025d5a7d534f8a6de448c940e17`)
extends the first segment to earlier score positions. Authored spreading follows
the worker's `ao`/`jr` rules, retaining the existing rational timing policy rather
than synth tick flooring. This preserves the site's supplied clock; it does not
certify that every source tab is musically synchronized.

Validation covers up/down brushes and arpeggios, fractional shifts, later tempo
changes, repeats, six-string openings, overlapping voices, genuine pre-recording
events, map identity and coverage, forged provenance, and a prepared FeedPak.
Release acceptance additionally requires fresh shared-app imports and package
verification for Battery and Paradise City, with Hybrid Lead enabled.
