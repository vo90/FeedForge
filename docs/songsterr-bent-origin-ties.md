# Plain continuations of ordinary bent attacks

Preservation contract 93 adds a narrow Songsterr continuation class to the
existing plain-tie identity policy. A note with an ordinary initial finger bend
may be followed by a plain explicit `tie: true` whose valid stored fret differs
from the held attack's fret. The continuation inherits that attack's target and
does not create an attack, pitch transition or new bend controller.

Qualification follows the performed occurrence, arrangement, voice and physical
string. The origin and continuation must be unique, and the previous authored
end must exactly equal the continuation's start. All intervening tied segments
must also be plain and exactly adjacent. Missing origins, overlaps, explicit
rest interruptions, unfilled gaps and unresolved links remain guarded for this
new class. Repeat entrances qualify separately for each visit and can inherit
different bent attack targets.

Only the first attack owns the finger-bend curve. Continuation-owned curves,
mute/harmonic changes, vibrato, whammy, slides, hammer-on/pull-off links, trills,
staccato and displaced attacks remain outside this class. An already resolved
incoming hammer-on/pull-off or slide is also a compound origin. Onset picking,
fingering, accent and ghost instructions may remain; palm mute, let ring,
tremolo picking, tapping, slap and pop combinations need separate qualification.
Retained source metadata such as an already validated `fadeIn` flag adds no
pitch controller. Existing equal-fret expression policies are unchanged.

The existing finger-bend resolver remains authoritative. For an ordinary initial
bend followed by plain ties, its normalized control positions cover the complete
held interval. This can move the release later than on the initial written
segment alone; the converter does not keep the short segment's curve and append
a guessed held tail. Tempo boundaries interpolate in quarter-note time before
conversion to seconds. A newly admitted group must finish with resolved bend
timing. Later continuations, even with the same fret, cannot introduce a
controller or an unqualified compound after this interpretation is established.

Raw source bytes and model frets remain intact. Playable fret, tied notation fret
and MIDI pitch use the same effective per-occurrence target. The existing
`import/plain-tie-identity.json` envelope and `songsterr-plain-tie-identity-v1`
policy remain stable. New bent-origin rows use
`plain-tie-keeps-bent-attack-target`; ordinary rows keep
`plain-tie-keeps-attack-target`. Each row retains authored/used frets, the exact
source hash, source/origin identities, voice/string and performed times. The
existing finger-bend timing evidence records the curve and its held endpoint.

Producer tests compare attacks, curves, endpoints, source lineage and effective
notation with already supported same-fret controls. The independent verifier
implements admission and evidence reconstruction separately. Hash-pinned native
worker fixtures qualify ordinary curves, authored/final clocks and repeat visits;
they do not claim acoustic or recording alignment correctness. Existing bend
grading remains coarse and does not grade every curve point.

The motivating retained source contains 23 differing-fret plain continuations
across 18 ordinary bent attacks. A bounded survey of 241 other retained sources
found no comparable differing-fret candidates, although existing bend and
same-fret bend-tie handling is common in that corpus. The rule uses musical
structure and performed continuity; it has no song, title, bar or envelope-version
exception. It does not resolve unrelated linked-technique blockers or approve
an otherwise incomplete song import.
