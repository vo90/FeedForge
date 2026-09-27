# Optional Songsterr Hybrid Lead

The verified Song browser → Songsterr import can append one **Hybrid Lead**
arrangement (`type: lead`). The option is off by default. All original guitar
and bass arrangements, their order, and the recording are retained. The older
manual converter and the Hybrid Track plugin are not involved.

## Import flow

Each queued job captures its own options. A sole guitar or sole unambiguous
lead is selected automatically. Multiple leads or several generic guitars pause
the job for a main-guitar choice; other queued jobs continue. Expand **Source
choices** before import to request that selection even for an automatic match.
The selection screen assigns primary-solo, additional-lead and accompaniment
roles, exclusions and preferences within each role. Names such as **Solo Chords**
and competing simultaneous primary parts require review. Even identical parallel
solos currently require an explicit preference; automatic equivalence detection
is not claimed. Choices are bound to the retained source SHA-256 and survive
restart/retry. Unsupported primary sources remain visible for an explicit choice.

A single guitar, or a song with no safe additions, still gets an identical
additional arrangement and a **No additions** result. With no usable guitar,
the original instruments import normally and Hybrid Lead is not applicable.
Failure never silently becomes a successful originals-only import. The explicit
fallback starts a separate job using the exact retained tab and recording choice.
Failed hybrid attempts retain their staged
files under the existing cache-retention policy and retain durable evidence.

## Musical policy: hybrid-lead-v2

`hybrid_primary.py` constructs a logical lead first: confirmed solo, main guitar,
then additional leads during primary rests. Accompaniment is considered only
afterwards. A solo can replace lower-priority main material, including where the
main returns before the solo finishes. The displaced main gestures are recorded;
the original main arrangement is unchanged. Non-ghost solo pickups and complete
connected gestures survive section labels. Separate ghost-only tails outside the
solo body are recorded as non-primary, allowing a return to the main guitar.

Written non-rest slots, sustains, forward technique links, source voice identity,
transitive grace relationships, strums, chords and trills protect complete
gestures. Exact end-to-start primary handovers are allowed. Staccato and existing
main high-fret omissions do not create artificial gaps. Unsupported or
incompatible primary sources request review instead of silently disappearing.

Donors must have the same physical tuning and capo. No transposition, revoicing,
stacking, or shortened notes is performed. Bass, effect/echo layers, duplicate
performances and donors with source high-fret omissions are excluded. Donor
passages come from global rests of at least one quarter-note beat, source
sections, or exact adjacent 1/2/4-measure repetitions. A barline alone is not a
phrase boundary. Internal rests remain part of the selected passage.

For optional accompaniment, the transition guard is the larger of 0.25
quarter-note beats and 125 ms in the actual recording map. Remaining primary
gaps need at least one beat after guards. Complete
passages from multiple donors can fill the same main gap in sequence, with one
donor at a time. Explicit source priorities are honored in order. Otherwise the
interval planner considers supported passage coverage within each main rest
region, with a one-beat band around its best coverage, then continuity, boundary
confidence and fret movement. Separate rest regions have independent searches;
the intervening main guitar remains authoritative.
Stable source IDs break ties. This is a conservative engineering policy, not an
assessment of taste or proof of comfortable playing.

## Conversion and evidence

`hybrid_context.py` captures performed written slots directly from the score,
including repeat occurrences and voice projections, independently of optional
staff notation. `hybrid_lead.py` plans in musical coordinates against the full
source and its recording map. Original alignment and omission policies remain
authoritative. Materialization copies whole events from the already retimed
original charts, so timing is applied once and all technique fields survive.
Chord templates are remapped without changing their contents. Optional practice
difficulty is generated from the finished derived chart.

`import/hybrid-lead.json` records policy/options, source and audio identities,
main and donor identities, roles, performed occurrences, event references,
source IDs, selected and displaced main events, passage boundaries, timestamped
event dispositions, rejected candidates, notation status and chart hashes.
`addedSeconds` measures selected span previously unoccupied by the main;
`selectedSeconds` also includes solo spans replacing the main. Neither is a
note-density score. The final archive hash belongs only in external evidence.
Original sidecars are unchanged; the derived receipt references their sources.
Unused templates containing the unpitched-mute sentinel are removed from the
derived chart; referencing chord IDs are remapped without changing any notes.

Preservation contract **33**, receipt version **2** and the explicit v2 request
policy prevent v1 results from satisfying new requests. `verify_hybrid.py`
checks copies, rhythm, boundaries, setup, notation, hashes and source lineage.
`verify_hybrid_priority.py` independently reconstructs primary requirements
from the raw-source reader, without importing the production planner. It audits
every available source event, kept/replaced main references, roles and coverage.
Removing a solo or substituting accompaniment fails even when the chart remains
an exact copy of the wrong selections. The queue requires this separate coverage
proof before publication. No check claims to measure musical enjoyment.

Written notation is composed from the selected source voices. When a source
already has a declared notation limitation, the derived result declares
source-only notation instead of presenting a misleading main-only staff.
TabView's companion update validates copy lineage before projecting original
strum and tied-harmonic evidence. The companion core update preserves **Hybrid
Lead** in smart naming and keeps the original automatic arrangement choice;
explicit or saved Hybrid Lead choices still work.

## Bounds and validation

Planning fails explicitly above 20,000 candidates or 10,000 Pareto states. This
preserves the safety rules on unusually complex tabs. Excluding donors reduces
the search. Unknown source features continue to use the importer's existing
compatibility gate. Ambiguous primary roles require review; unsupported primary
coverage cannot report automatic success. Optional accompaniment can still leave
rests, with reasons recorded. Labels and tab contents cannot identify every
audible part with certainty; human audition remains necessary.

Tests cover source-choice/resume, multiple donors, ordinary/single/bass-only
imports, unchanged original payloads, nonlinear timing, difficulty, written
staccato/omissions, internal rests, strums/chords, slides, trills, grace notes,
harmonic ties, voice projection, solo replacement, exact primary handovers,
ghost tails, ambiguous/parallel leads and deliberately missing/substituted solos.
Real-song corpus builds
and consumer checks are recorded in the implementation handoff. Auditioning
the generated arrangements remains the musical acceptance step.
