# Finger bends, explicit bar curves and terminal slide-outs

A continuous tied fret can now use the established finger-bend clock while
retaining an explicit whammy-bar curve and one terminal, direction-only
slide-out. The two previously independent qualification rules rejected each
other, causing a fallback to individual written bend segments.

Both constituents must qualify over the complete tie: same fret, string and
voice; contiguous valid ties; noncompeting finger controls; explicit retained
bar points; and one final tied slide-out. Incoming, intermediate and targeted
slides, displaced attacks, harmonics and other unqualified expressions remain
guarded. No song ID, title or hard-coded musical position affects eligibility.

The bar keeps its own curve and interval, and the slide keeps its authored
direction and marks. Neither is added to the finger-bend scoring pitch. The
existing optional whammy and visual slide policies are unchanged. The native
FluidSynth worker may shorten a tied synthesis note to make space for slide
samples; those generated samples are not authored fret targets or grounds to
compress written bend/bar controls.

The independent verifier reconstructs this composition from source atoms.
Packages containing it require preservation contract 81 and finger-bend
evidence version 20. The game wire schema is unchanged. Only the specific
qualified bend-timing warning is removed; other compatibility findings remain.

Regression fixtures capture eight synthetic sources and independently verified
constituent outputs from before implementation. Coverage includes both slide
directions, slow/early/late/precise bends, tempo changes, repeated passages,
negative mixtures, nonlinear recording maps, Hybrid Lead and archive mutation
rejection. Existing imported songs are not migrated; re-import uses the new
timing and evidence.
