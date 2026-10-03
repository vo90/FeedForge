# Recording-end cutoff with source timing warnings

Contract 57 lets an otherwise valid original-recording import finish when the
source tab has passages whose synchronization cannot be confirmed. The existing
cutoff removes attacks at or beyond the audio end. It does not retime earlier
notes, extend the recording or substitute another revision or recording.

## Acceptance and evidence

The selected approved revision, original video, completed Songsterr timing map
and recording identity must agree. The map remains finite and strictly increasing.
The acoustic assessment must cover the recording and corroborate at least three
non-overlapping passages. An inconclusive or suspected mismatch elsewhere is
recorded as a warning, not changed into a successful acoustic assessment. A wholly
unconfirmed recording still requires matching audio.

`recordingEnd.timingWarning` records the general policy, uncertain time ranges in
the original recording, and `everyNoteVerified: false`. It is retained in the
FeedPak, evidence store and job summary. The original source, timing map, acoustic
report, removed attacks and shortened tails are preserved as before. There are no
song-specific conditions or fixed limits on the number of ending bars omitted.

The independent package verifier checks source fidelity and reproduces the
warning from packaged audio. It does not equate a successful conversion check
with perfect musical synchronization. Modified earlier notes, missing notes,
incorrect omission ledgers, mismatched video identity, silent substituted audio,
incomplete gestures and partial strums still fail. Optional preparation silence
does not change warning locations in the original recording.

FeedForge displays **Imported with timing warnings**, explains that source timing
was retained, shows the uncertain recording ranges and the ending omission count.
Hybrid Lead uses the same accepted source timeline and undergoes its normal
independent checks. No setting or migration of existing imports is needed.

## Tests

Real synthetic audio with a deliberately imperfect source tab covers faithful
conversion, the ending cutoff, preparation silence, persisted warnings and
independent revalidation. Negative tests alter the source projection, warning,
recording identity, audio and omission ledger. UI tests check the warning and
ensure it never claims synchronization was confirmed. Existing verified cutoff,
padding, recording identity and source-fidelity regressions remain applicable.

This extends contract 56's cutoff evidence gate documented in
`songsterr-ending-suffix.md`; supported recordings keep their existing behavior.
