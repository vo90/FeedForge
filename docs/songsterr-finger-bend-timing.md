# Finger-bend timing on completed notes

Preservation contract 59 resolves ordinary Songsterr finger bends after ties.
An immediately following tied segment with a bend ends the preceding gesture
at its onset. Otherwise the original attack uses its completed sounding end;
later hidden continuations use their own segment end. A tie adds no attack.
Initial unbent spans and held pitch between gestures are explicitly represented.
Two points at a segment boundary preserve a later change without an earlier ramp.
Tempo boundaries are sampled in musical time before recording-time mapping.
Source notation, frets, attack count and sounding duration are unchanged.

The existing contract-31 tied-staccato resolver and its guards remain separate.
Ordinary chains combining slides, whammy, displaced attacks or overlapping bend
controls retain their previous segment interpretation and receive a compatibility
limitation. This does not claim reference playback parity for those combinations
or silently choose precedence between overlapping controls. They remain in the
source and the compatibility backlog for separate investigation.

`import/finger-bend-timing.json` binds source identities, segment and gesture
intervals, resolved/deferred status and resulting score-time curves to the source
SHA-256. The independent verifier reconstructs it from its own source atoms and
rational clock. Missing or altered evidence, old contracts and incorrect chart
curves fail verification. Existing imports are never rewritten.
Evidence identities and structure must match exactly; timestamps use the existing
1.1-microsecond chart tolerance and pitch values allow only floating-point error.
This avoids false failures from rounding equivalent timestamps separately.

Reference: captured public Songsterr worker SHA-256
`4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab`.
Tie scheduling (`ks`) runs before finger bends (`xo`). Synth one-tick offsets and
pitch quantization do not become extra chart attacks or musical timing offsets.
Research qualified 40 synthetic cases in both authored/player-defaults profiles,
four real guitar parts, and a tempo-change probe against the entire captured
worker. This is a pinned behavior reference, not a dependency on live site code.

Validation covers tie lengths, delayed bends, release, chords, separate voices,
repeats, guitar/bass, tempo and recording maps, retained source and package mutation
tests. Back in Black bar 4 and Enter Sandman bar 85 demonstrate the previously
accelerated bend/release; Master of Puppets bars 265–266 demonstrate a late bend.
There are no song, artist, revision, instrument-program or fret exceptions.
