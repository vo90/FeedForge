# Staccato with pitch gestures

The public performer applies staccato after folding tied segments and before
generating pitch gestures (bend, hammer/pull and slide synthesis). Its sounding
duration is half the note interval, with a 1/128-whole-note minimum. The written
duration and attack time remain unchanged. Thus an untied staccato note may
also carry a bend or slide; rejecting every combination was overly broad.

Evidence: `FluidsynthAudioPlayerWorkerEntry-Do-Dwi4LNDxTupzE.js`, captured
23 September 2026, SHA-256
9bc2e262f42077e5f6d13d7c67269d8e92c2e24f8d6ff47ca9601b235251c33f.
The generator order is `ks` (ties), `us`, `Cs` (staccato), followed by pitch
gesture handlers including `Ss` (slides). `Cs` changes `offTick` relative to
`onTick`, leaving source notes intact. The earlier captured public worker
contains the same order and formula.

Conversion keeps the authored slide type/target and bend curve over the
shortened performed interval. Written notation stays separate. Plain tied
staccato and verified tied finger bends are covered by `songsterr-tied-articulation.md`
and `songsterr-staccato-bends.md`; other combined tied pitch gestures remain
explicit limitations. The importer never creates an attack to make a tie pass.
