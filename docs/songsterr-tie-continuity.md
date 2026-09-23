# Tied continuation through unwritten time

The public `ks` performer matches a tied continuation to the preceding note on
the same string in the same voice, extends that origin to the continuation's
endpoint, and hides the continuation's attack. It does not require the prior
written duration to fill the intervening measure. Enter Sandman's clean guitar
begins with an eighth note in a 4/4 measure and ties that note into measure 2.
The authored tie provides a definite endpoint; no extra attack is inferred.

Songsterr imports now preserve this sustain while retaining both original
written durations. They still reject missing origins, changed frets, overlapping
logical endpoints, explicit rests inside the gap, and unresolved repeat jumps. The source performer's separate
fret-repair routine is deliberately not applied.

Simultaneous hidden continuations across voices are not duplicate attacks.
Actual conflicting same-string attacks remain blocked; no voice is discarded.
GPIF's existing continuity contract is unchanged.

Evidence: public worker `FluidsynthAudioPlayerWorkerEntry-Do-Dwi4LNDxTupzE.js`,
captured 2026-09-23, SHA-256
`9bc2e262f42077e5f6d13d7c67269d8e92c2e24f8d6ff47ca9601b235251c33f`.
