# Linked-technique diagnostics

The existing timeline guards for unresolved slides and hammer-ons/pull-offs now
report source coordinates and performed occurrence. A failed import still
requires all requested arrangements to be convertible; this change does not
publish partial songs or reinterpret a missing destination.

The `technique.linked_targets` rule adds structured evidence to the existing
compatibility report: the active marking's source identity, string, fret, mute
state, score time and occurrence; the attempted destination; or the repeat/end
boundary where the link remained unresolved. A marking on a tied continuation
points to that segment, rather than misidentifying the initial attack. Resolved
links are removed from diagnostic state. Details are bounded, with explicit
truncation indicators. Source identities and original source bytes remain the
authoritative record.

Coordinates and occurrences shown to the user are one-based. Structured string
numbers run from low to high. Times are score seconds **before recording
alignment**, not timestamps in a YouTube video or the final game audio. A fretless
mute is `null` in these details, never a playable fret 127 or an inferred fret 0.

This is a diagnostic change to contract 53. Successful chart output and the
independent verification calculations are unchanged. It requires no migration
of existing packages and does not relax any acceptance guard.

## Development reference evidence

`linked-target-cases.cjs` creates 24 synthetic shift/legato combinations with
open and fretted origins, attack/tied markings, and explicit/fretless targets.
`linked-target-reference.cjs` regenerates observations only from the pinned,
reviewed local worker into a new output file. Regeneration checks determinism,
source immutability and trace noninterference. The extracted runner was also
checked against the complete captured worker for all 24 examples under both
reference profiles, including generated synthesis events.

The checked-in fixture records an important distinction: the sampled source
player assigns internal fret 0 to a dead note without a fret. That is synthesis
behavior, not an authored open-string instruction. Tests keep these missing
targets blocked while proving that explicitly pitched targets still convert.
No source-player fret fallback, shortened synth release or generated MIDI
event is thereby approved as gameplay.

Offline checks:

```text
python -m pytest tests/test_songsterr_link_diagnostics.py tests/test_songsterr_muted_slides.py tests/test_song_import_arrangement_diagnostics.py
```

Changing the blocked behavior requires a separate general musical policy,
independent output/evidence verification and package acceptance tests.
