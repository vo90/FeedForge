# Songsterr note-local dynamics

Preservation contract 124 accepts an absent/null `note.velocity` or one of the
literal labels `ppp`, `pp`, `p`, `mp`, `mf`, `f`, `ff`, `fff`. Other values are
rejected, including false, zero and empty containers, even on rests or excluded
arrangements. A note marking is independent of the existing beat marking.

The written note's optional `dyn` field preserves its authored label. This is
an additive notation-v1 field with **note scope**; existing beat `dyn` is
unchanged. It never changes a chord companion, subsequent beat, or sounding
attack inherited through a tie. Written tied continuations retain their own
marking without creating a fresh attack. Let-ring does not propagate the label.
The original source always retains the complete field and location. If the
written sidecar cannot be produced, its existing omission policy still applies.

FeedBack's current highway and TabView do not show this field or apply individual
note loudness. The converter reports one grouped display/expression limitation
with source locations. Null fields and fields on excluded arrangements or rest
notes remain routine source diagnostics. Backing audio, pitch, timing, trails
and scoring are unchanged; this is not loudness grading or synthesized-audio
equivalence.

The independent verifier reads the raw field independently, compares written
note labels and scope, and requires complete compatibility accounting under
contract 124. Historical contracts retain their historical interpretation.
Mutations that drop, change, duplicate or move a marking are rejected.

Qualification used retained public worker SHA256
`a7f191f19196af972bd61ed5f9c67a6b63151fc8b0b8a1f665061a0cb493c980`.
Full-score and synthetic controls demonstrated local-over-beat precedence,
tie and let-ring behavior, accents and ghost notes. Numerical MIDI velocity is
profile-dependent: `mf` yielded 87 in one Fluidsynth profile and 50 in VST.
Therefore the contract stores the authored label, not a hard-coded amplitude.
No vendor implementation is shipped with the converter.

This change does not broaden natural-harmonic support. A score containing both
note dynamics and unsupported harmonics remains blocked on the latter.
