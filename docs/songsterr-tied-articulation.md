# Staccato after tie resolution

Songsterr's public performer folds a tied chain into its first attack (`ks`),
then shortens staccato notes (`Cs`). Shortening each parsed segment before link
resolution incorrectly made continuous authored ties look like gaps. The
converter now retains logical durations and applies articulation to the complete
chain. A staccato mark on a hidden continuation alone does not shorten the first
attack, following the source performer. Written ties and source marks survive.

Known-answer tests cover originating and continuation marks, cross-bar ties,
unresolved fret/gap rejection, and ordinary slide/bend articulation regressions.
The independent verifier uses logical source events before mapping their sounding
ends to the recording. The frozen real examples are Under the Bridge, Additional
Guitar measure 60, and Another Brick in the Wall Part 2, lead measure 86.

Combined tied staccato with pitch gestures remains a located technical limitation
pending verification of each hidden segment's gesture timing; it is not a reason
to reject ordinary tied staccato or a gameplay decision.

Source: public worker `FluidsynthAudioPlayerWorkerEntry-Do-Dwi4LNDxTupzE.js`,
captured 2026-09-23, SHA-256
`9bc2e262f42077e5f6d13d7c67269d8e92c2e24f8d6ff47ca9601b235251c33f`.
