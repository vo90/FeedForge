# Continuous ties at repeat entrances

Contract 47 keeps a sounding note across a backwards repeat jump only when the
next written measure starts with an explicit tie on the same voice, string and
fret, and the prior note ends exactly at the performed repeat boundary. Existing
tie technique and notation handling then extends that same note. The tie does
not create another attack or chord box.

This is source interpretation, not note repair. Gaps, rests, missing origins,
changed frets and dangling slides or hammer-ons/pull-offs remain unsupported at
the repeat jump. State unrelated to an explicit matching entrance is discarded.
The existing GPIF behavior is unchanged. Original source, written ties and every
performed source occurrence remain retained in the package and independent
verification.

The pinned Songsterr worker's native `ks` stage merges these tied continuations
before later synthesis-only strum and envelope adjustments. The reviewed worker
SHA-256 is `4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab`.
The development reference generator covers guitar and bass, open and fretted
notes, single notes and chords, one- and two-measure loops, repeat counts and both
scheduling profiles. Tests use its saved known answers; they do not regenerate
expected music from converter output.

Converter and verifier keep their own repeat-boundary calculations. Tests check
attacks, sustain ends, source occurrence accounting, retained tie notation,
Hybrid packaging, rejected invalid cases and deliberate package corruption.
This rule says nothing about original-recording synchronization, which remains a
separate import check.
