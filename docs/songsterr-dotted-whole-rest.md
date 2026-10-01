# Silent whole-rest boundary

The source scheduler clips a lone dotted whole-rest to the current measure's
boundary when its encoded duration exceeds that measure. FeedForge now accepts
that same silent span, retaining the original duration, dots and source bytes.
The following bar and all attacks keep their authored times.

This applies only to a single rest-only beat, `type: 1`, one through four dots,
and the exact corresponding whole-note fraction. Grace notes, tuplets,
contradictory dot/duration pairs, ordinary rest sequences and played notes do
not qualify. A dotted rest shorter than the bar keeps its shorter duration.
Explicit pickup inference is unchanged. There is no general note truncation.

Evidence: the compatibility lab's pinned public worker
`4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab`
was run on undotted, dotted and double-dotted rest examples across 2/4, 4/4,
6/4 and 8/4. Both preparation and the following scheduled attack confirm this
boundary. Local corpus replay also covers Anastasia and the separately acquired
Cryin' source. Source-timeline success is not an audio-aligned FeedPak result.

Production and independent verifier recognize the condition separately. Tests
cover meter, dots, unchanged source, repeats, another voice, rejected overflows,
and archives with a deliberately moved following attack. Existing notation-v1
limits for three/four dots still retain staff notation in source evidence only;
they do not invent a different glyph. Preservation contract is 42.
