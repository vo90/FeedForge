# Explicit strums and written continuity

The source performer runs explicit strums (`Ts` / `ao`), then folds written
ties (`ks`), then applies staccato (`Cs`). A shifted hidden continuation does
not create another attack, change its origin's picking direction, or break an
otherwise continuous written tie. Staccato on the origin shortens the span
from its shifted attack to the folded chain's end.

The importer keeps the written position and duration separate from the attack
offset. This permits authored strums crossing a bar boundary without changing
notation or inventing an extra pick. Fractional shifts within 0–100 are valid.
Unresolved or fret-mismatched ties, attacks before the score origin, and
nonpositive sounding durations remain explicit failures. No clipping or source
repair is performed. Rational timing continues the existing authored clock;
synthesizer MIDI-tick quantization and automatic humanization are excluded.

Evidence: public `FluidsynthAudioPlayerWorkerEntry-Do-Dwi4LNDxTupzE.js`, captured
2026-09-23, SHA-256
`9bc2e262f42077e5f6d13d7c67269d8e92c2e24f8d6ff47ca9601b235251c33f`.
