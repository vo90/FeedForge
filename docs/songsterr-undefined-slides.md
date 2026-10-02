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
