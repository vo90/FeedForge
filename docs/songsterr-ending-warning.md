# Importing a bounded ending with a timing warning

Preservation contract 39 separates source fidelity from confidence in acoustic
timing. An inconclusive ending can be imported with an explicit warning; it is
never relabelled as an acoustically supported ending.

This fallback applies only to the original recording identified by the retained
Songsterr map for the same song and revision. The recording must have a complete,
ordered sequence of assessed windows starting at its beginning and reaching its
end. All windows before the uncertain suffix must be supported, including at
least three non-overlapping windows wholly before that suffix. The uncertainty
must be confined to the final 32 seconds or 20% of the recording, whichever is
shorter. No window or joint window assessment may report a suspected mismatch.
Alternative recordings, manual audio, mismatched identities, missing body
evidence and uncertainty earlier in the song do not qualify.

Existing ending eligibility remains mandatory: every source attack starts
inside the original recording, padding is at most two seconds and only the
minimum samples needed for an existing final note tail are added. The existing
mixed policy may shorten eligible long held/muted tails before preserving the
remaining short gesture. The fallback grants no new permission to omit attacks,
shorten active long gestures, change pitches or alter source timing. Any already
supported opening repair remains separately declared and verified.

`endingPadding.timingWarning` records `source-map-ending-warning-v1`, the
uncertain interval, independent body-window count and the warning. The original
`endingPaddingSync` assessment stays inconclusive. The warning is re-evaluated
after encoding; the package verifier reconstructs eligibility from raw-source
events and remeasures the original audio, then compares the warning receipt and
complete acoustic report. Missing, changed or unsupported warning receipts fail
verification. Older contracts cannot use this exception and retain their
original verification behavior.

The app displays "Ending sync not independently confirmed" on the completed
import. The same limitation survives in the package, evidence bundle, import
notes and independent verification report. Successful source verification means
the conversion preserved the source under the declared ending rules; it does
not claim the recording's every note was independently matched.

This is an explicit acceptance policy, not a new acoustic matching algorithm or
a guarantee that an unconfirmed ending is correct. It is general and contains
no artist, song, revision, fret or timestamp exceptions.

Tests: `test_song_import_ending_warning.py`, existing ending/source-preservation
suites, and `songsterr-ui.test.cjs`. The real encoded fixture uses a sustained
ending that supplies too few distinct attacks for automatic clock support.
Shifted recordings, unrelated reversed audio, silence, wrong identities,
interior uncertainty, excessive unknown endings, missing warnings and forged
receipts are tested separately.
