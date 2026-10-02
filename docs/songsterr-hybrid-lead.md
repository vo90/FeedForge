# Optional Songsterr Hybrid Lead

Songsterr import can append one **Hybrid Lead** (`type: lead`). The option is off
by default. All original guitar/bass arrangements, their order, notation and
audio remain intact. Composition happens inside FeedForge.

## Automatic selection and fixed tuning

Ordinary imports automatically choose a usable base, including ambiguous or
generically named guitars. Source choices can still request manual review.
Explicit main, roles, exclusions and preferences are bound to the retained tab
hash and survive retries. Unknown roles do not stop a usable import.

Automatic selection determines the dominant guitar tuning from normal activity,
counting a union rather than rewarding duplicate tracks. Within that tuning it
prefers a suitable main/lead source, whose capo is then fixed. Brief differently
tuned solos cannot change the output tuning. All donors must match its strings,
tuning and capo exactly; compatible material substitutes where available.
There is no transposition, re-fingering or mid-song retuning.

Explicitly named high-strung/Nashville layers with corroborating octave-displaced
lower strings do not set the tuning when an ordinary guitar tuning supplies
non-solo activity covering at least half the texture layer's activity. This is a
deterministic selection heuristic, not a universal musical rule. Sole high-strung
sources still work, and a brief differently tuned solo cannot displace a long
high-strung song. Names or high pitches alone do not trigger this safeguard.

A single guitar retains the straightforward source-preserving path. With no safe
additions the base is copied as Hybrid Lead. With no usable guitar, original
instruments import normally and Hybrid Lead is not applicable. Malformed sources
and invalid output still fail; they never silently become an originals-only job.

## Regional policy: hybrid-lead-v3

`hybrid_selection.py` separates performer, musical function and tone. Named
soloists, dedicated solo activity and corroborated local musical evidence can
establish regional ownership. The base has a continuity preference, not permanent
priority over another guitarist's solo. Scoped labels such as Solo Chords,
Pre-Solo and non-guitar solos do not automatically identify a primary guitarist.
Clean and polyphonic parts can carry the lead.

Selection revision **4** asks what the lead guitarist would play locally. Among
ordinary tracks, lead/rhythm/clean labels are weak clues. Local articulation,
melodic texture, rhythmic variation and corroborated repeating accompaniment
patterns carry more weight. Neither absolute register nor note density earns a
solo by itself. A named player's rhythm-labelled track can contain their solo.
Close alternatives retain coherent ownership and low confidence; receipts expose
the considered sources, local measurements and selection reason.
Extra and harmony labels normally favor a credible ordinary lead, but cannot
hide a clear foreground line when all ordinary alternatives demonstrably play
backing. Echo/effect layers are excluded from automatic primary ownership;
an explicit source-review solo assignment remains respected.

`hybrid_regional.py` assembles complete primary candidates and protects successive
soloists. Explicit guitar-solo sections outrank inferred chorus/riff material, so
a preceding inferred tail cannot earn more activity by displacing a solo opening.
Alternate-voice coverage requires every omitted whole gesture to be covered by
another selected lead or solo. Partial handover conflicts are disclosed.
Unavoidable overlap between complete gestures at a named handover is
reported as limited coverage. Simultaneous leads use coherent alternatives, without
claiming to copy every independent performance into one guitar. Chords, ties,
forward technique links, grace relationships and source voice identity preserve
complete gestures. Same-source polyphony is retained together.

`hybrid_handover.py` refines chosen featured episodes inside genuine written
rests. A compatible credible peer may contribute a complete melodic response
while every already-selected owner event stays intact. An otherwise unknown-role
source can qualify in a generic solo through strong local expressive/melodic
evidence and a non-backing parent context; a missing performer name alone does
not exclude a clear response. Automatic layer/effect exclusions and explicit
accompaniment choices remain respected. Both base and donor peers
use the same path. The physical episodes split around the response; independently
verifiable original reservations keep unrelated backing out of the remaining
solo rests. Continuous parallel harmonies remain one coherent voice. A response
whose sustain crosses the owner's return is not cropped to fit. The receipt
records local handover evidence and deterministic generation limits.

A featured owner's unsupported gesture can also leave a playable hole. A
compatible known or manually assigned lead may substitute there only when raw
source evidence proves every authored note in the occupied slot belongs to an
unsupported complete gesture at that performed occurrence. Missing or partial
evidence keeps the slot protected. Supported owner notes and every selected
event remain intact; the substitute must itself contain complete supported
gestures. This applies between selected fragments of the same featured passage
as well as inside an episode. The original unsupported-source limitation stays
disclosed even when another guitarist supplies a playable replacement.

Primary attack windows are distinct from complete event footprints. Pickups and
tails can cross section markers without absorbing later unrelated backing notes.
Small conflicts are resolved locally. Arbitrary cross-source note layering is
not supported. Compatible lead/solo sources can also provide filler outside
protected primary regions.

Optional phrases use supported rests, sections and repeated patterns. Internal
rests stay part of a phrase. The transition guard is the larger of 0.25 quarter
beats and 125 ms under the actual recording map, with at least one beat remaining
in a guarded gap. Primary handovers may use zero guard. `hybrid_search.py` uses a
bounded chronological graph, useful activity, explicit source preferences and
continuity costs. Every optional role uses this path, including additional lead
tracks. Fixed primary material before and after a gap contributes entry and
return costs, reduced when an authored rest permits repositioning. A fill is
compared with leaving that gap empty. `hybrid_variants.py` also offers bounded
prefix/suffix/interior alternatives when an enclosing phrase crosses primary
material or a transition guard. Touching same-source repeat phrases can also
form a bounded combined candidate within a gap, so separate phrase recognition
does not prevent a meaningful complete fill. Omission boundaries remain barriers.
A section marker can be crossed by two touching same-source parents only when
one, two or four complete bars repeat exactly across it, with matching authored
notes/techniques and written rhythm, stable meter/tempo and consecutive score
traversal. The original marker and bounded proof remain in `variant.sectionJoin`;
another interior section, missing source material or a repeat jump blocks the
join. Section names do not confer permission. It favors existing musical boundaries, then safe
bar cuts or complete source-group boundaries for syncopated ties. No event,
written occupied slot or connected technique is sliced. These fallback variants
need at least four active quarter beats and two pitched attacks, and pay a
boundary cost in addition to entry/return costs. Original complete phrases keep
their prior eligibility. Short incomplete responses can remain unfilled.
Mandatory short solos retain their primary path.
Search limits retain a feasible result. Unsupported local
gestures do not exclude every supported phrase in the same source.

After preserving the complete baseline plan, `hybrid_optional.py` offers one
bounded refinement around already selected optional neighbors. It uses original
source parents, whole gestures and the same full-gap transition costs. Additive
changes must retain every incumbent event and avoid reducing existing utility.
Optional neighbors do not receive fixed-primary rest/movement cost relief.

`hybrid_optional_foreground.py` recognizes conservative local melodic episodes
among optional candidates, including gaps where the base lead is silent. It
requires expressive variation in several complete gestures and corroborated
accompanying texture in competing parts. Repetition, high register, labels or
dense activity alone do not establish musical priority. Ambiguous competing
melodies, expressive double-stop solos and repeated lead riffs retain the
existing choice. Explicit preferred sources and manual foreground roles win.
Proposals apply to exact candidate events, never a track-wide preference boost.

A foreground replacement can intentionally remove accompaniment within its
episode. Surrounding accompaniment is retained at complete source boundaries.
The narrowly scoped `retained_optional_boundary` variant can preserve a short
existing prefix/suffix with at least two pitched attacks, even below four active
beats. It must reach its original outer edge, identify the complete old parent
and actually selected foreground, obey the same source-change guard, and is
limited to one prefix and one suffix per episode. This does not lower the
minimum for newly introduced fills or allow deletion of primary quiet notes.
An isolated outer accompaniment event that cannot form such a retained fragment
may be omitted only within a disclosed local choice envelope: fewer than two
pitched attacks per edge, no quiet/ghost notes, and at most one quarter beat or
20% of the foreground span, whichever is smaller, across both edges together.
The receipt records exact omitted references, prior parent and selected episode.
Longer surrounding passages and explicit preferred-source activity must survive.

These are deterministic heuristics, not calibrated probabilities or a guarantee
that every ambiguous musical choice matches a player's preference. The local
handover pass recovers credible responses during actual owner rests; it does not
establish every foreground change while both voices are playing. Listening and
playing review remains necessary for those cases.

## Source copying and independent evidence

Planning uses performed score coordinates, including repeat occurrences. Existing
alignment and endpoint policies remain authoritative. Materialization copies
supported events from already retimed originals, retaining technique fields and
applying timing once. Templates are remapped without changing note contents.
Unused template shapes above fret 24 are pruned because their muted/pick-scrape
validity requires actual chord references. Referenced shapes remain intact.
Optional practice difficulty is generated from the completed derived chart.

`import/hybrid-lead.json` records options, source/audio identity, fixed setup,
regional evidence, obligations, selected/replaced events, source IDs and
occurrences, dispositions, limitations and hashes. Automatic exclusions retain
their actual origin. The final archive hash belongs in external evidence.

Structural validity, primary coverage, musical confidence and bounded search are
separate concepts. A valid result can have limited coverage because a lead has
incompatible tuning or an unsupported gesture. Accepted simultaneous voices are
explained as alternatives. `addedSeconds` and `selectedSeconds` describe selected
passage spans, not uninterrupted playing or note density.

Preservation contract **37**, receipt version **3**, selection revision **4** and
the explicit v3 policy
prevent prior results from satisfying new requests. Historical policy audit
paths remain; older evidence is not presented as current verification. Queue
publication requires current independent proof tied to the exact staged file.

`verify_hybrid.py` checks exact copies, setup, timing, whole boundaries, notation,
hashes and lineage. It uses the continuous independent score/recording clock when reconstructing
quarter-note coverage positions. Six-decimal serialization rounding is applied
only to comparisons against stored recording timestamps, never inside the
inverse search. This avoids a systematic early boundary on short timing-map
intervals without widening coverage tolerances or changing the musical output.

`verify_hybrid_priority.py` independently reconstructs hard
source relationships and conservative named/dedicated solo requirements without
importing selection code. Busy rhythm cannot satisfy another guitarist's solo
merely by changing receipt roles. Its categorical melody/backing contrasts are
separate from the producer's numerical ranking. Unsupported omissions need local
evidence, including wholly unsupported named solos. The audit declares its scope:
it does not prove every unnamed foreground choice or subjective musical quality.
Counts distinguish requirements in playable charts from named requirements
containing unsupported raw source material that need local disclosure.

`verify_hybrid_opportunities.py` additionally checks recoverable peer gestures
inside named parallel-solo rests and clear expressive unknown-source responses
inside actual remaining chart rests in generic solo sections. The generic check
recognizes simple held-note lead incumbents separately from the stronger evidence
required to demand an unknown response. It does not demand replacing a sounding
alternative voice. Named-owner checks retain their stronger rule that ordinary
backing cannot hide a missing soloist. Optional source opportunities are
reported separately as review candidates, not mandatory note-count targets.
An additional check reconstructs raw unsupported owner and donor groups to
detect compatible lead alternatives left out of proven unsupported-owner holes.
Projected low-fret endpoints of an unsupported connected gesture cannot alone
prove that either the incumbent or a replacement is playable.
The verifier reconstructs complete boundaries and event membership independently;
variant metadata or a handover label alone cannot authorize a cut or addition.
`verify_hybrid_continuity.py` independently reads raw authored patterns, meter,
tempo, traversal and source-parent geometry for section joins.
`verify_hybrid_optional.py` checks the complete source parent, retained boundary,
selected foreground membership and guards for short retained accompaniment.
These physical checks do not certify subjective foreground ranking or replay
the producer's previous optimization as an independent musical oracle.

`complete` coverage means identified lead requirements are accounted for; it
does not mean every moment of source guitar activity has been filled. The
`tabActivity` report measures unions of retained non-ghost guitar note durations
in recording time. It reports unfilled spans separately for incompatible setup
and available compatible material. The verifier recomputes these values from the
verified original charts and actual Hybrid Lead. These are tab durations, not an
audio analysis, and projected-out high frets remain in their separate omission
report. The import result exposes unfilled regions of at least one second.
`restWindows` groups source activity inside each continuous Hybrid Lead rest,
even across short donor rests, and distinguishes new pitched attacks from held
or muted material. Per-source attack counts avoid rewarding duplicate guitars;
zero-duration played attacks interrupt a rest without inventing sustained time.

Notation is selected by source identity/occurrence as well as phrase times.
Where a contributing source has a notation limitation, the result declares
source-only notation. Companion TabView support validates copied events and
projects only their strum/harmonic evidence. Core preserves Hybrid Lead naming
and original default routing; explicit/saved Hybrid Lead choices still work.

## Validation

Tests cover fixed tuning, incompatible solo fallback, alternating soloists,
single-guitar behavior, original preservation, nonlinear timing, connected
techniques, same-source polyphony, source order, duplicate sources, bounded
fallback, local omissions and deliberately forged membership/ownership evidence.
Small optimizer instances are compared with exhaustive enumeration. A bounded
actual-source Master of Puppets fixture requires James's 66-event melodic solo
on the rhythm-labelled track and rejects self-consistent backing substitutions.
Further regressions cover base/nonbase local handovers, preserved owner events,
syncopated complete-group variants, small guard conflicts, source-boundary
mutations, and conservative short-fragment exclusion.
Unsupported-owner fallback tests cover exact source identity and occurrence,
partial proof, retained parallel voices, donor playability and unchanged owner
events.

Per-song retained-corpus results are recorded separately. This corpus informed
the design and is a regression set, not an unbiased generalization estimate.
Held-out listening/playing review remains necessary for release-level musical
acceptance, especially ambiguous harmonies and handovers.
