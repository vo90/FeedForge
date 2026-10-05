# Artificial harmonics with terminal slides

A continuous tied artificial harmonic may retain its harmonic target while
its pitch bends, vibrates and ends with an upward or downward slide-out.
The terminal flourish does not set the finger-bend clock.

The converter qualifies the complete composition only when:

- The first attack has a supported artificial harmonic target, which remains
  unchanged in the output. Later ties repeat that target or omit the marking.
- Every segment stays on the same fret, string and voice, with continuous ties.
- The slide-out is the final tied segment and specifies direction only.
- Finger bends do not compete. An overlapping controller must have finished
  changing before the next controller begins, including exact-boundary cases.
- Any vibrato has a separately identified written interval and intensity.

Explicit target changes, delayed harmonic contacts, targeted slides, incoming
slides, bar controls and other unqualified mixtures retain the existing fallback.
No fret, attack, harmonic pitch, vibrato instruction or slide direction is inferred.
Native synthesizer slide notes and sample shortening are not chart instructions;
the final written bend retains its authored interval.

Packages using the composition require preservation contract 82 and finger-bend
evidence version 21. Independent rational reconstruction verifies the eligibility,
curve, harmonic policy and slide evidence without importing the producer.
Older contract and evidence versions cannot certify this composition.

Regression coverage includes captured upward/downward cases, repeated or omitted
targets, strict overlap boundaries, tempo changes, repeats, chords, beat vibrato,
Hybrid Lead, nonlinear alignment and mutations of packaged chart/evidence data.
The change uses existing FeedBack fields; it does not introduce a gameplay rule.
