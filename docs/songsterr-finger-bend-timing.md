# Finger-bend timing on completed notes

Preservation contract 60 resolves Songsterr finger bends after ties.
An immediately following tied segment with a bend ends the preceding gesture
at its onset. Otherwise the original attack uses its completed sounding end;
later hidden continuations use their own segment end. A tie adds no attack.
Initial unbent spans and held pitch between gestures are explicitly represented.
Two points at a segment boundary preserve a later change without an earlier ramp.
Tempo boundaries are sampled in musical time before recording-time mapping.
Source notation, frets, attack count and sounding duration are unchanged.

The existing contract-31 tied-staccato resolver and its guards remain separate.
An overlapping controller can hand off only after its entire remaining curve is
flat and its final update is at the completed note-off. Its timing stays based
on the original complete tie, but its effective curve ends at the later bend's
onset. The later bend then controls the pitch; the old note-off reset is not an
extra gameplay bend. Explicit jumps preserve both boundary values. Later plain
ties hold the latest bend value. No time-gap or pitch-difference tolerance grants
this qualification: even a small remaining slope is a conflict.

Changing overlaps retain their written segment interpretation and receive a
specific compatibility warning. This is the approved source-first fallback,
not a claim of native synthesis parity. Overlaps involving additional expression
(vibrato, harmonics, mute, palm mute, let ring, HO/PO, tremolo, trill or scrape)
remain explicitly deferred. Existing slide/whammy/displaced-attack guards apply.
The native scheduler can interleave independent pitch updates; that behavior
must not become invented extra bends or altered attacks in the chart.

Version 2 of `import/finger-bend-timing.json` binds source identities, segment and gesture
intervals, resolved/deferred status and resulting score-time curves to the source
SHA-256. The independent verifier reconstructs it from its own source atoms and
rational clock. Missing or altered evidence, old contracts and incorrect chart
curves fail verification. Existing imports are never rewritten.
Overlap evidence also records each source-to-source handoff, its original
controller end and its clear/conflicting/compound classification. Required
fallback warnings are independently checked in the compatibility report.
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

Overlap research replayed 61 passages in 35 complete parts from 31 retained song
sources. Nine full parts and 15 isolated cases were qualified in both native
profiles (48 full-worker/extracted-harness comparisons). Of the 61 passages,
31 had no competing updates, eight had only note-off-boundary conflicts, and 22
had mid-note conflicts. These are investigation categories, not unconditional
permission to resolve every case: compound expression and changing controls
still fail the narrower production guard. Native-controller reference tests
allow only the fixture's documented discrete-step/quantization bound; independent
source-to-package verification retains its original strict pitch tolerance.
