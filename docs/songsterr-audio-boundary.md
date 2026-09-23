# Consistent recording-end checks

This describes the original strict boundary fix. Contract 10 adds the explicit,
independently verified held-tail exception in `songsterr-terminal-sustains.md`.

The Hotel California Solo benchmark reached final verification after the source
map and builder allowed playable sustains up to 50 ms beyond the recording.
The independent verifier correctly rejected those notes. This was an alignment
constraint reported too late as a conversion mismatch, not permission to clip
the notes or relax completed-package verification.

Source-map acceptance and package construction now allow only the existing
microsecond serialization tolerance. The builder checks rounded archived timing
as well as unrounded timing, for both standalone and chord notes and both source
maps and affine fallback alignment. Diagnostics retain mapped end and recording
duration. A source-map rejection still permits the existing independent matcher
with unchanged confidence requirements.

Tests cover 10 microsecond, 20 ms and 49 ms overruns, exact endpoint notes,
chords, affine alignment and unchanged bends. Contract 8 is unchanged: no
previously verified package's musical contents or acceptance criterion changes.
