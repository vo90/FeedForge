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

`hybrid_regional.py` assembles complete primary candidates and protects successive
soloists. Unavoidable overlap between complete gestures at a named handover is
reported as limited coverage. Simultaneous leads use coherent alternatives, without
claiming to copy every independent performance into one guitar. Chords, ties,
forward technique links, grace relationships and source voice identity preserve
complete gestures. Same-source polyphony is retained together.

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
continuity costs. Search limits retain a feasible result. Unsupported local
gestures do not exclude every supported phrase in the same source.

These are deterministic heuristics, not calibrated probabilities or a guarantee
that every ambiguous musical choice matches a player's preference.

## Source copying and independent evidence

Planning uses performed score coordinates, including repeat occurrences. Existing
alignment and endpoint policies remain authoritative. Materialization copies
supported events from already retimed originals, retaining technique fields and
applying timing once. Templates are remapped without changing note contents.
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

Preservation contract **34**, receipt version **3** and the explicit v3 policy
prevent prior results from satisfying new requests. Historical policy audit
paths remain; older evidence is not presented as current verification. Queue
publication requires current independent proof tied to the exact staged file.

`verify_hybrid.py` checks exact copies, setup, timing, whole boundaries, notation,
hashes and lineage. `verify_hybrid_priority.py` independently reconstructs hard
source relationships and conservative named/dedicated solo requirements without
importing selection code. Busy rhythm cannot satisfy another guitarist's solo
merely by changing receipt roles. Unsupported omissions need local evidence.

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
Small optimizer instances are compared with exhaustive enumeration.

Per-song retained-corpus results are recorded separately. This corpus informed
the design and is a regression set, not an unbiased generalization estimate.
Held-out listening/playing review remains necessary for release-level musical
acceptance, especially ambiguous harmonies and handovers.
