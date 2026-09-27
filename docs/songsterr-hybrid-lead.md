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
The selection screen can exclude or prioritize supplementary guitars. Choices
are bound to the retained source SHA-256 and survive restart/retry.

A single guitar, or a song with no safe additions, still gets an identical
additional arrangement and a **No additions** result. With no usable guitar,
the original instruments import normally and Hybrid Lead is not applicable.
Failure never silently becomes a successful originals-only import. The explicit
fallback starts a separate job using the exact retained tab and recording choice.
Failed hybrid attempts retain their staged
files under the existing cache-retention policy and retain durable evidence.

## Musical policy: hybrid-lead-v1

The main guitar remains fixed for the whole song. Written non-rest slots,
sustains, linked techniques, grace relationships, strums, chords and trills are
protected across all strings. Main notes omitted by a declared source limitation
still protect their original region. Staccato is not interpreted as extra rests.

Donors must have the same physical tuning and capo. No transposition, revoicing,
stacking, or shortened notes is performed. Bass, effect/echo layers, duplicate
performances and donors with source high-fret omissions are excluded. Donor
passages come from global rests of at least one quarter-note beat, source
sections, or exact adjacent 1/2/4-measure repetitions. A barline alone is not a
phrase boundary. Internal rests remain part of the selected passage.

The transition guard is the larger of 0.25 quarter-note beats and 125 ms in the
actual recording map. Main gaps need at least one beat after guards. Complete
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
main and donor identities, performed occurrences, event references, source IDs,
passage boundaries, rejected sources/alternatives, notation status and chart
hashes. The final archive hash belongs only in the external evidence store.
Original sidecars are unchanged; the derived receipt references their sources.

Preservation contract **32** retains every original-source check and adds an
independent derived-arrangement checker. That checker uses the independent raw
source reader, not the production planner, to check protected rhythm, guarded
gaps, whole boundaries, exact original-event membership, source lineage, setup,
notation and hashes. It rejects unexpected extras and altered requests. It does
not judge whether the composer's musical preference is enjoyable.

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
compatibility gate. No claim is made that every audible guitar part will be
included: ambiguous or incomplete passages are deliberately left as rests.

Tests cover source-choice/resume, multiple donors, ordinary/single/bass-only
imports, unchanged original payloads, nonlinear timing, difficulty, written
staccato/omissions, internal rests, strums/chords, slides, trills, grace notes,
harmonic ties, voice projection and corrupted outputs. Real-song corpus builds
and consumer checks are recorded in the implementation handoff. Auditioning
the generated arrangements remains the musical acceptance step.
