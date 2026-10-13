# Plain ties across written rests

Songsterr can continue the latest note on the same voice and string through a
written rest when the later note explicitly carries `tie: true`. The continuation
keeps the attack's pitch, even when the tied note stores a different fret. It
extends that attack's sustain rather than creating another attack.

The importer supports this general case under preservation contract 122. The
origin and every continuation must be unique and plain, with no active pitch,
articulation, strum, or timing controls. Intervening beat and rest controls are
checked too. The chain must follow consecutive written measures; repeat jumps,
skipped endings, overlaps, orphan ties, and cross-voice guesses are not qualified
by this policy. An intervening same-string attack replaces the origin. Notes on
other strings remain separate attacks.

This is a separate rule from ordinary adjacent ties. Once a chain crosses a rest,
later continuations must continue to satisfy the plain-note rule. There is no
arbitrary gap-duration cutoff. Tempo changes use the existing score clock, and
Hybrid Lead sees the extended sustain as occupied time.

The original source stays byte-for-byte intact. Notation retains its written rests
and durations, with the tied note's effective fret matching the sounding origin.
`import/rest-ties.json` records source identities, visits, rest spans, authored
and effective frets, and score times for each qualified continuation. The manifest
uses `restTiePolicy: songsterr-rest-tie-continuation-v1`. Compatibility inventory
records `note.rest_tie` as `source_interpretation` / `source_retained`, which does
not create a player warning.

The independent verifier reconstructs the rule and receipt from its own source
reader. It rejects missing, extra, duplicate, or altered evidence and unsupported
contract or inventory versions. Packages made under older contracts retain their
previous tie interpretation. Alignment maps playable times; the receipt remains
in score seconds.

## Qualification

The retained public Songsterr worker and a reviewed reference were compared on
plain rest gaps, whole-voice and whole-measure rests, other-string activity,
repeated continuations, changed stored frets, long gaps, and tempo changes. Both
matched the four rest bridges in the unchanged Classical Gas revision 240775.
The pinned worker SHA-256 is
`4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab`.
This qualifies that reviewed behavior, not every future Songsterr implementation.

Regression tests cover the producer, independent evaluator, historical gates,
notation, voice projection, Hybrid occupancy, alignment, and archive tampering.
This policy alone does not establish that the full recording and tab are in sync.
