# Artificial harmonic playback fallback

Preservation contract 123 keeps a verified sounding pitch separate from an
unspecified physical harmonic touch position. For an explicit artificial harmonic
with a finite nonnegative `harmonicFret` that misses Songsterr's node lookup,
the public performer uses +24 semitones. The held fret and timing are unchanged.

The new target is exactly:

```json
{"kind":"artificial","node":null,"interval":24,"policy":"harmonic","basis":"source-playback-fallback"}
```

Canonical four-field targets retain their existing meaning. The game displays
AH without a touch number; native harmonic target capability 2 is required for
the fallback. Delayed physical contact events still require a known node. Source
values remain in the immutable score and compatibility ledger, with one grouped
player warning explaining the missing touch instruction.

The beat-level `artificialHarmonic` flag is retained metadata when every sounding
note explicitly qualifies as artificial. It never creates harmonic notes.
Contradictory, dead, silent-only or malformed active flags remain guarded.

## Proactive coverage

The fallback reference matrix tests canonical nodes, lookup tolerance boundaries,
unknown numeric positions, other harmonic kinds, missing/null/string/boolean
values and legacy `harmonicData` branches against the public performer. These
branches are not all equivalent gameplay instructions: missing semi-harmonic
nodes can create a second synthesized voice, and malformed legacy data can
produce a nonfinite pitch.

Values inside the source lookup's 0.001 tolerance must never become +24 fallback
targets; noncanonical near-node positions remain guarded until a separate
physical-contact mapping is qualified. Missing/null nodes, other harmonic kinds
and opaque legacy data are not broadened by this change. The compatibility
catalog records the verified rule and reference tests; no vendor code is shipped.
