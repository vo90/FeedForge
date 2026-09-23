# Grace groups and authored measure boundaries

The converter allocates grace time to authored beats, including rests and explicit
rakes. A before-beat group at the start of a later bar borrows the previous
voice's remaining bar time; the previous sounding note is shortened only if
necessary. The written measure and original grace mark remain intact. At score
start the source player uses on-beat timing for a before-beat group that has no
preceding beat. Two groups borrowing from one principal use its original budget.

These rules follow the public Songsterr performer captured on 2026-09-23:
`FluidsynthAudioPlayerWorkerEntry-Do-Dwi4LNDxTupzE.js`, SHA-256
`9bc2e262f42077e5f6d13d7c67269d8e92c2e24f8d6ff47ca9601b235251c33f`.
The `Mi`, `bi`, and `Ci` routines were evaluated independently against twelve
frozen real arrangements (6,922 beats). All rational onsets and durations matched.
The capture, extraction harness, and comparison receipt are retained in the
external `songsterr-coverage-next-20260923` verification directory.

Source data is never changed. The package verifier calculates the clock
independently. Cross-bar grace groups involving repeated traversal and adjacent
cross-bar grace groups remain explicit technical limitations pending additional
source traversal verification; these restrictions are not gameplay choices.

Coverage includes grace rests, rest principals, source-defined rake spreads,
cross-bar note and silent-tail borrowing, initial fallback, shared principals,
and repeat-context rejection. Existing swing, grace, repeat, and tempo tests
remain part of the regression suite.
