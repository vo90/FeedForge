# Finger bends with qualitative bar vibrato

Contract 75 allows otherwise qualified, non-overlapping finger bends to use
their completed tie clock when a beat also has `vibratoWithTremoloBar`.
The bar control must be `slight` or `wide`, with no explicit or inherited held
bar-pitch curve anywhere in the note. Slides and displaced attacks remain
excluded. Overlapping bend controllers retain their previous limitation;
this rule does not clear their other-expression guard.

Songsterr's reviewed worker separates bar modulation (`Ko`) from finger-bend
pitch events (`xo`). Paired native probes with and without the bar marking
preserved finger-bend events and attack/tie times in both authored and player
profiles. The reviewed worker SHA-256 is
`4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab`.
These probes establish control independence, not acoustic equivalence or an
exact physical vibrato depth/rate. Synth tick offsets and discrete pitch
updates do not replace the continuous authored finger-bend curve.

The existing bar regions, intensities, optional policy, source identities,
attacks, ties and other supported expressions are preserved. No bar marking
is removed to qualify the bend, and no measured bar pitch is invented.
FeedBack's existing visual and scoring policy is unchanged.

Finger-bend evidence version 15 adds `barVibrato` with policy
`independent-qualitative-control` and source-bound marking intervals.
The independent verifier reconstructs the qualification and bend clock from
its own parsed source atoms. Packaging rejects older contracts for these
passages; archive verification checks both bend evidence and the separately
preserved whammy data. Unaffected bend evidence keeps its existing version.

Regression coverage includes guitar/bass, slight/wide markings, placement
across ties, tuplets, repeats, tempo changes, explicit/held/zero bar curves,
slides, displaced strums, overlapping bends, piecewise audio alignment,
Hybrid Lead, original source preservation and corrupted archive rejection.
The retained-source investigation identified 17 qualifying passages: 14
equivalent curves losing an unnecessary warning and three Free Bird lead
tracks whose bend was reaching its target too early. Eligibility uses source
structure only, never a song name, ID or revision.
