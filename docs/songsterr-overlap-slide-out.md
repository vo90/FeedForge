# Settled bend handoffs with a terminal slide-out

Preservation contract 69 combines two independently qualified source rules:
settled bend-controller handoffs and a tied terminal direction-only slide-out.
It does not assign a target fret, create another attack, repair the source tab,
or change FeedBack scoring/rendering.

The initial native bend controller can span the complete tied note when its
immediate continuation has no bend. If a later continuation has its own bend,
the earlier curve must already be flat at that handoff. Compressing the first
curve into its written segment makes the bend happen too quickly.

Qualification requires every overlapping earlier controller to be settled at
the exact authored handoff and to end at the complete note end. The outgoing
cue must be on the terminal tied, fretted segment, have a supported direction,
and finish at the same note end. Explicit note vibrato retains its separate
source interval. Conflicting controls, other slide combinations, beat vibrato,
harmonics, displaced attacks, whammy, HO/PO and the existing expression guards
remain unqualified. Tied staccato stays in its own resolver.

Eligibility uses exact source positions: a bend still changing at 50.001% is
not settled at a 50% handoff. Native tick/position tolerances apply only to the
captured synth comparison, never to that decision. Native sample transposition
and slide-synthesis shortening do not alter authored playable fret or timing.

The independent verifier reconstructs the rule from source atoms and rational
positions without importing the producer. Newly qualified packages require
contract 69 and finger-bend evidence v10, including
`overlap.slideOutTiming = independent-terminal-cue`. Original source bytes,
attack, sustain, frets, slide cue and vibrato intervals are preserved. Existing
imports are not rewritten; re-import with the updated converter.

Regression coverage includes 49 synthetic source cases captured against the
reviewed public Songsterr worker in authored and player-default profiles, both
directions, tempo changes, repeats, chords, independent vibrato, exact boundary
guards and later controllers. Package tests cover Hybrid Lead, piecewise audio
mapping, downgrade rejection and mutations of curves, cues, evidence and note
data. The fixture contains synthetic sources only. Its pitch samples use the
worker's actual pitch range and sample-compensation shift.
