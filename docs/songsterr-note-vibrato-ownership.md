# Note-owned Songsterr vibrato — preservation contract 94

New Songsterr imports use `fingerVibratoPolicy: songsterr-note-vibrato-v1`.
Finger vibrato belongs to an explicit note `leftHandVibrato`, `vibrato` or
`wideVibrato` instruction. Modern note instructions keep their existing
precedence. Beat `vibrato` and `wideVibrato` remain in the original source,
written beat annotations and source-bound compatibility findings. They do not
create note `vb`, `vibrato_marks`, note vibrato glyphs or scoring targets.

This corrects the previous, explicitly disclosed written fallback. The pinned
native player reads note instructions; it does not fan these two beat flags
onto other sounding notes. A mixed beat can contain a fresh note with its own
vibrato and a plain hidden continuation without one. The continuation keeps its
established target and completed endpoint through the existing plain-tie policy.
The fresh note retains its own vibrato over its completed held group. No attack,
fret, sustain or controller is inferred from the beat annotation.

Genuine note controllers keep the timed-vibrato rules: an initial instruction
extends over its completed hold, while a later controller owns its written
interval. Resets stop the active instruction without resuming an older one.
Tempo/repeat mapping, slight/wide strength, clipping and Hybrid Lead retain the
same note-relative interval representation. Vibrato remains qualitative display
expression, never an exact pitch oscillator or bend/scoring curve.

The producer marks fresh imports with the policy; contract 94 packages record
it in their manifest and recipe. The independent reader reconstructs note-owned
intervals for these packages. Historical contracts through 93 retain their
original interpretation: the producer's explicit
`vibrato_policy='songsterr-written-beat-vibrato-v1'` mode and the independent
historical reader preserve the previous fallback and its bend receipts. The
default parser never silently chooses that mode. Old source/native reference
fixtures remain immutable.

`__beat_vibrato` describes only the historical fallback controller. New imports
do not leave it on metadata-only notes, so bend eligibility and timed controller
receipts cannot mistake a retained beat annotation for a pitch gesture. Original
beat facts remain in source evidence and compatibility coverage. Existing bend
resolvers continue to handle explicit note controls and historical receipts.

This change does not admit differing-fret ties with an actual continuation-owned
vibrato, bend, bar controller, harmonic or unresolved slide/HOPO combination.
It retains the existing negative-fret mute expression guard. The separate
pending-HOPO continuation proposal remains unimplemented.

Tests cover mixed/all/unmarked chords, rests and ties, modern precedence,
initial and late controller intervals, per-occurrence notation targets,
source/model immutability, the actual twelve-note held-group shape, protected
gestures and historical bend references. Corpus/native evidence distinguishes
removed fallback instructions from changes to attacks, pitch, timing or source.
