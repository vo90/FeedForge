# Undefined slides ending on an explicit mute

Preservation contract 54 applies a general, approved display limitation to
Songsterr shift and legato links. If the next attack on the same source voice
and string is explicitly `dead: true` and has no fret, retain both written
events and omit the undefined connecting slide. A fretless X does not establish
a direction, a destination pitch, or an open-string instruction.

The rule preserves note attacks, strings, durations, ties and other supported
effects. It adds no muted endpoint, pitch, slide-out direction, HO/PO or legato
scoring flag. A separately specified incoming slide or directional slide-out
keeps its existing interpretation. Both pitched-to-X and supported muted-shift
to-X relationships are covered. A tied X is not treated as a new attack by
this rule; ordinary tie validation still applies.

Missing targets, repeat-boundary links, unpitched HO/PO and invalid source
fields remain guarded. The change does not reinterpret other formats or
modify old FeedPaks. Fresh imports use the new policy.

Contract 55 separately qualifies an orphaned slide whose alternate-ending
destination is skipped before an explicit rest. See
`songsterr-skipped-ending-slides.md`; it does not remove the general link guards.

Each omission appears as `note.undefined_slide_to_mute` in the compatibility
report and in `import/undefined-slides.json`. The receipt contains source
identities, written locations, traversal occurrences and score times, the
original outgoing slide kind, the explicit X destination, the applied rule
and the original source hash. The complete original tab is embedded unchanged.
Combined incoming/outgoing instructions remain recoverable from that source.

The independent verifier reconstructs the relationship from raw source using
its own parser and clock. It checks preserved notes and the mandatory receipt,
including target identity, timing, mute status and omission coverage. Missing
evidence, removed or moved X events, duplicate attacks, invented slide targets
and inappropriate legato flags fail verification.

Tests cover both source slide kinds, hidden-fret muted origins, fretless muted
origins, tied segments, repeats, mixed chords, separate voices, incoming-slide
combinations, boundaries and deliberate package corruption. Captured player
fixtures remain evidence of synthesis behavior only: an internal fallback to
fret zero is never copied into the playable chart.

Contract 84 extends the same omission to an explicitly dead destination encoded
with fret zero. Zero on an X does not establish an open pitched destination.
The retained X keeps its original encoding (zero or missing), ghost marking,
time and duration. Ordinary open-string destinations, nonzero muted positions
and direction-only slides keep their existing handling. A zero-muted omission
uses undefined-slide evidence version 3; missing-fret-only archives retain
their existing version.

A continuous, same-fret tied finger bend may now be verified after omitting its
terminal slide. The producer and independent verifier must each establish the
exact per-occurrence omission, including the source and target identities and
timing. Bend evidence version 23 retains that proof as `omittedTerminalSlide`.
It does not change the bend curve or normalize conflicting expressions: gaps,
overlaps, incoming slides, harmonics, whammy and other unsupported combinations
retain their guards. The intentional slide-omission notice remains visible;
only the redundant bend-timing limitation clears for a proven case.
