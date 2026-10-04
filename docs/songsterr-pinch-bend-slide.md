# Pinch-harmonic bends with a terminal slide-out

Contract 76 qualifies non-overlapping finger bends on one held pinch harmonic
with a terminal, direction-only slide-out. The initial bend uses its completed
tie clock until another authored bend begins. Later bends use their own source
segment intervals. Finger vibrato and the slide-out's written interval stay
independent. Neither the held fret nor the attack count changes.

The initial valid pinch target must remain the output target throughout one
continuous tied note on the same fret, string and voice. Later pinch markings
can repeat, omit or change their target under the established initial-target
continuation policy. Changed targets retain their separate harmonic ambiguity
finding and archived source. This rule does not establish a new harmonic target
or remove that finding. Timed contact and other harmonic kinds remain excluded.

The full gesture must also pass the existing terminal-slide guards. Overlapping
bends, explicit or qualitative bar controls, targeted or intermediate slides,
incoming slides, displaced attacks, HO/PO, mutes, let-ring, tremolo, beat vibrato
and other incompatible controls are not qualified by this composition.

The public worker reference has SHA-256
`4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab`.
Paired controls establish that the harmonic pitch target does not change bend
event timing. MIDI pitch quantization occurs after base-pitch adjustment, so
normalized synth values can differ by less than one MIDI quantization step.
Source and archive verification retain their existing stricter tolerances.

Native slide synthesis may shorten the last segment's synthesized sound and
its bend, depending on the instrument profile. Preserve the authored segment
clock, as in the existing terminal-segment rule. Do not import synthesized
slide pitches, invent a destination fret, shorten the note or add an attack.
The game continues using its existing fading directional trail and harmonic
and bend controls; this adds no rendering or scoring policy.

Finger-bend evidence version 16 adds `continuedPinchHarmonic` to
`terminalSlideOut`, containing the initial target and `initial-target-continued`
policy. The independent verifier reconstructs eligibility, target, timing and
evidence from its own parsed source atoms. Both builder and verifier require
contract 76. Fresh imports receive the fix; existing FeedPaks are not rewritten.
