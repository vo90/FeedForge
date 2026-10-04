# Generated chart guidance

The verified Songsterr/Guitar Pro song-import builder now completes the final
arrangement with fret-position suggestions and conservative ordinary handshape
spans. This runs automatically on the imported originals and on a completed
Hybrid Lead, after recording retiming/composition and before difficulty and
archive hashes. It changes no musical events, pitches, strings, durations,
techniques, tuning, capo, chord names, finger assignments or source notation.

This feature is independent of the source-preservation contract. The import
recipe declares `chartGuidancePolicy: feedforge-chart-guidance-v2`. Each chart
has `ext.chartGuidance` with the policy, explicit field ownership, a digest of
its musical inputs, a digest of its generated guidance, and diagnostics for
wide positions and chords skipped by the handshape policy. `sourceAuthored`
and `fingeringAssessed` are false. Changes to this policy require a new policy
identifier in producer, independent checker and desktop acceptance code.

## Position policy

- The lane is inclusive: `{fret: 3, width: 4}` covers frets 3 through 6.
- Normal windows are at least four frets wide, clipped to the 1–24 neck. Wide
  simultaneous/sounding material gets a wider window rather than lost notes.
- Positions cover fretted attacks, active sustains and known slide corridors.
  Guidance occupancy ends at the next attack on that string; the original note
  sustain is never shortened. Zero-duration attacks constrain their onset only.
- Open strings, unpitched muted strikes and pick-scrape reference frets do not
  invent a hand position. Natural harmonic contacts use the contact location;
  harmonic pitch intervals are not interpreted as finger positions.
- An ordinary dead strike's stored editor fret is unpitched, even when it is
  numerically within the neck. Explicit slide, harmonic and other motion cues
  retain their positional interpretation. Palm mute alone remains pitched.
- A still-suitable window stays in place. Half a second of bounded lookahead
  breaks ties when selecting a new position. An unusually wide window returns
  to four frets only after three coherent local attacks span at least a second.
- The first useful position is available during the lead-in. Open passages and
  rests retain context. There is no whole-song average-fret camera target.

Known slides reserve their complete start/end corridor for the duration, rather
than trying to reproduce a particular renderer's easing curve. This can be wide
and is deliberately approximate. Direction-only slide decorations and tapping
do not supply a guessed destination or fingering. These remain visible through
their existing technique renderers. A position suggestion is not a certification
that a human can hold a wide simultaneous voicing with one hand.

## Handshape policy

Only actual simultaneous chord events with exact template/string/fret agreement
are eligible. A span starts at the real chord attack and ends at the earliest
member release or new attack on a member string. Touching spans of the same
template can join; gaps never create ringing. Every repeated chord attack stays
in the chart. Open-string chords are eligible.

Slides, bends, harmonics, linked/HOPO events, fret-hand mutes and other complex
contact/motion cases are skipped conservatively. Explicit arpeggio template
flags and the arpeggio name conventions recognized by the 3D highway are not
converted into ordinary handshapes. This version does not infer arpeggios, chord
names, fingers, or high-density flags. The existing repeat renderer remains in
charge of compact-repeat display and technique visibility.

Generated shapes begin at existing real chord onsets, including when chord wire
times round to milliseconds. They must not cause the renderer's handshape
fallback to synthesize an additional visual strum.

## Preservation and validation

`chart_guidance.finalize` fills absent fields. Existing authored arrays remain
unchanged. Generated arrays can be rebuilt only with recognized provenance and
an intact guidance digest; manually edited or unknown-provenance fields cause
an error rather than being silently overwritten. Repeating the same operation
is deterministic and idempotent. A changed musical input requires explicit
regeneration. Hybrid materialization explicitly regenerates after adding donor
events, before its final chart hash is computed.

The independent `verify_chart_guidance` module imports no generator helpers.
It checks musical-input binding, ownership, legal ordered inclusive positions,
coverage of active note/slide/contact requirements, exact supported handshape
coverage, absence of unsupported synthesized onsets, and the diagnostics.
Builder validation and final source-to-archive verification both use it. Source
and Hybrid musical comparisons remain exact; only the two declared generated
fields are separately checked for the derived chart.

Generated difficulty gets its own guidance from the events retained in each
level. Positions and handshapes are bounded by the phrase. Top-level musical
data remains unchanged. This avoids a hidden hard-level note dragging a simple
practice level's lane to an irrelevant position.

The worker explicitly requires the guidance policy. The desktop additionally
requires matching current guidance evidence in the durable source-verification
report and response, bound to the actual completed archive hash. Reuse compares
the complete recipe and checks the prior guidance evidence. Removing all
guidance declarations cannot downgrade a new worker result to a legacy success.
Historical packages and evidence remain readable without being certified as
having this new guidance.

## Scope and integration

This change covers the verified song-import builder and its committed Hybrid
Lead path. It does not replace the older standalone Songsterr converter's
generator, change PSARC-authored guidance, rewrite existing song libraries,
change repeated-chord rendering, or enable unnamed chord diagrams in the game.
Those are separate changes. New imports must be made with the matching updated
converter and desktop acceptance code.

The implementation branch starts at Hybrid Lead commit `dd4c602`, while that
feature and the main Songsterr test integration continue developing elsewhere.
When integrating onto a newer Hybrid materializer, preserve/rebuild the
`ext.chartGuidance` ownership after the final note selection and before
difficulty generation and all chart/receipt hashes. If a newer materializer
clears `ext`, it must also clear only its verified generated anchors/handshapes
before calling the finalizer; leaving inherited base-only arrays and removing
their ownership would incorrectly make them look authored. Preserve the exact
musical comparison and keep generated-field verification active in the final
archive checker. Do not change or reuse a source-preservation contract number
to bypass a version conflict.

Tests include literal independently authored archive expectations, boundary and
wide chords, all-open/unpitched material, 4–8 strings, overlaps and releases,
slides and harmonic contacts, pick scrapes, repeats, regeneration/edit guards,
difficulty windows, randomized interval coverage, Hybrid donor positions,
archive tampering with recomputed digests, legacy archives, and desktop
publication rejection when guidance evidence is absent or mismatched.

## Validation recorded 2026-09-27

- Full Python suite: 1,696 passed, 5 skipped. A final focused run covering
  guidance, source verification, Hybrid, builder and pick scrapes passed all
  126 tests after tightening diagnostic counter types.
- Full Song Browser JavaScript suite: 634 passed, 2 skipped.
- Read-only corpus dry run: 469 arrangements across 96 saved packages passed
  independent guidance checks and idempotence, with all existing musical data
  unchanged. This produced 28,353 anchors and 46,828 ordinary handshape spans;
  2,461 anchors required more than four frets.
- Actual 3D renderer helpers checked 1,486 generated handshapes in 12 sampled
  arrangements, producing 1,144 valid playing regions and no additional
  synthetic chord attacks.

The corpus work transformed copies in memory. These checks do not establish
visual comfort or fingering quality during live playback; no live WebGL
playthrough was performed, and existing library packages were not rewritten.

## Positionless passage preparation

The `positionless-preparation-v1` position policy keeps timed slide-following
and extends open pickups to plain dead strikes, open fret-hand mutes, mixtures
of open/dead strings, open tremolo and open whammy effects. Dynamics and picking
marks do not prescribe a fret. A dead strike's hidden editor fret is ignored;
positive frets with only palm/fret-hand mute still require their written fret.

The whole connected run adopts the next fretted passage's lane. Existing gap
limits, simultaneous fretted holds, contact/technique constraints and linked
gesture boundaries remain protected. Both ends of an explicit link or HOPO
boundary are considered. Harmonics, slides/scrapes, bends and finger vibrato
are not treated as freely relocatable open pickups. Camera technique focus is
separate from lane placement and is unchanged.

New imports record the new policy. Explicit regeneration accepts intact older
receipts, including `slide-follow-v1`; ordinary finalization preserves them.
The identical Core generator upgrades verified older positions in memory,
including phrase levels, without writing song archives or changing music.

Tests exercise Cirice's two muted chords before the 7/9 chord, mixed groups,
placeholder frets, tremolo/whammy, incoming/outgoing links, retained contacts,
active strings, rest boundaries, receipt upgrades and independent fret coverage.
