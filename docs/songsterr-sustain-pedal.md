# Songsterr sustain pedal

Preservation contract 45 recognizes the optional boolean `beat.sustainPedal`.
It is retained in the immutable source, field inventory and located compatibility
findings, including markings on rests. False/absent/null is inactive; other
values are invalid, even when falsy. Piano and other excluded instruments remain
outside the guitar/bass import scope.

The pinned Songsterr scheduler merges marked performed beat spans and emits MIDI
controller 64 values 127/0 on the instrument channels. This controls the synth's
damper pedal. Sampled final note attacks and releases do not change; actual
synthesized ringing can change. A guitar chart must not automatically turn these
controller events into longer trails, new attacks or a let-ring instruction.

FeedBack keeps the existing written/tied notes and scoring. The compatibility
report discloses that pedal expression and its engraving are not represented.
The source recording remains the backing audio; it is not resynthesized. The
independent verifier validates the flag separately and derives required report
entries from the source. Removing warnings, altering pitches/timing/durations or
inventing let-ring effects must not pass.

Reference qualification covers rest, tie, repeated and overlapping-voice spans
on guitar/bass under authored and default scheduler profiles. Regenerate into a
new file using `node tools/songsterr_compatibility/sustain-pedal-reference.cjs
WORKER COMMON_SCHEMA NEW_OUTPUT`. The adapter requires the exact reviewed asset
hashes. Normal tests use the synthetic fixture; released imports do not execute
external player code. Existing FeedPaks are not rewritten.
