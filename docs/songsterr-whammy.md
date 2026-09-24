# Songsterr whammy preservation

Preservation contract 17 carries exact signed bar curves and slight/wide bar
vibrato through conversion, alignment, archive verification and retry evidence.
The additive `whammy` note extension uses version 1 and optional-expression
policy. Raw source remains archived; fret, ties, chord ownership, other
techniques and original-audio selection are unchanged.

Points use note-relative song seconds and signed semitones. Source tone units
are divided by 50; positions use sixtieths or complete precise percentages.
Duplicate coordinates keep the last value. The source's two-point preset form
and first-tone reset into tied continuations are retained. Explicit held
segments preserve that reset across unmarked ties without extra attacks.
Qualitative bar vibrato never creates a numeric pitch curve except to retain an
already active, independently established constant bar position.

The independent reader, timeline and archive comparison do not import the
producer's normalization helper. Mutation tests check pitch, time, ownership,
policy, structure and presence. Retiming inserts breakpoints at alignment
boundaries. Existing terminal-cutoff rules cannot cut through a moving bar
gesture. Unknown active structures still produce located diagnostics.

Updated game consumers support the extension. Bar expression is optional;
unassessable compound/qualitative events are visual only with located reasons,
not misses. No hardware question is added to the import flow. Tab View retains
exact data even where its quantized GP display cannot draw a precise curve.

Tests: `tests/test_songsterr_whammy.py`, the song-import/semantics suites, and
Songsterr browser job/evidence tests. The paired workspace workflow separately
checks packaged imports, rendering and both scoring paths with generated audio.
