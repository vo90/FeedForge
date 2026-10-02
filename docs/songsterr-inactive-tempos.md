# Tempo entries beyond the written score

Preservation contract 48 retains and reports a valid tempo instruction whose
measure is beyond the final written bar, without applying it to another bar.
This is permitted only when the first source tempo is explicitly at bar 0,
position 0, and gradual tempo is disabled or no linear ramp is present.
Every entry still undergoes field, type, finite-rate and nonnegative-coordinate
validation. Negative bars and positions, within-bar overruns, unknown semantics,
initial-clock shifts and active-ramp interactions remain blocking.

This rule follows reviewed Songsterr preparation: step tempos attach only to
existing measures. It does not remove a note, repair a tempo, invent a measure,
or certify recording synchronization. The original entry remains in source
evidence and receives a `tempo.outside_score` compatibility finding. Independent
source evaluation qualifies the context separately; package verification derives
the required entries from source and detects missing or altered accounting.

The development fixture uses worker SHA-256
`4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab`.
Its 64 cases cover two deterministic profiles, repeats, fermatas, enabled/disabled
gradual flags, different missing bars and positions, and dotted note values.
Native preparation clocks, emitted tempo events and final note scheduling are
unchanged when the inactive entries are absent. A counterexample in each profile
demonstrates that an enabled ramp changes the prepared clock through `Ur`'s
missing-offset fallback. The subsequent synthesized schedule need not expose
that difference, which is why preparation is checked separately.

Tests use local reviewed expected traces. The app never loads external player
code. GPIF handling and previously imported packages are unchanged.
