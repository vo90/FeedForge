# Targeted slides and finger bends

Preservation contract 80 adds optional `slide_interval: {start, end}` on a
pitched note with `sl`. Both times are seconds relative to that note's attack.
They identify the written segment carrying a resolved shift/legato slide,
including a final tied continuation. They do not specify the exact physical
slide speed or copy a synthesizer's generated intermediate notes.

The source link must have a continuous, unchanged fretted/string/voice origin,
no displaced attack, staccato or earlier targeted slide, and an immediately
adjacent pitched destination. An uncertain or muted destination retains the
existing interpretation and warning. Nothing guesses a destination or repairs
the tab. Guitar Pro/PSARC output and existing archives are unchanged.

An independent parser reconstructs the interval from source atoms. Packaging
maps both endpoints through the audio clock (including piecewise mapping),
preserves them through Hybrid Lead, and rejects missing/modified intervals.
The compatibility inventory advertises the new extension. A targeted slide
crossing a recording cutoff still cannot be silently stretched or shortened.

Finger bends on eligible ties follow their own authored clock. Non-overlapping
ordinary finger controls may coexist with the terminal slide; competing bends,
bar controls, unresolved destinations and other unqualified compositions retain
their guards. The evidence uses version 19 when this qualification applies.
Source points, note attacks, link kind, destination fret and scoring semantics
are preserved. No combined audible pitch oracle is claimed.

FeedBack loads and forwards the optional bounds with microsecond precision.
The 3D renderer applies its existing slide easing only inside them. Generated
lanes and crossing samples follow that same interval. Files without the field
retain whole-sustain slide motion. The 2D slide arrow remains a direction cue.

Validation includes synthetic native finger-controller references (worker SHA
`4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab`), independent
archive mutations, tempo/mapping/Hybrid tests, retained approved source replay,
and consumer transport/geometry checks. Native quantization is tolerated only
in the reference comparison; archived timing checks retain their existing
microsecond tolerance. Historical archives are not rewritten.
