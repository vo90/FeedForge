# Legacy dotted rhythm

Older Songsterr revisions can mark a dotted beat with `dotted: true` instead of
`dots: 1`. Contract 85 accepts that spelling in guitar and bass arrangements.
This is a field interpretation, not a source migration or timing correction.

The general rule follows the hash-pinned Songsterr player: a positive `dots`
count takes precedence; absent, null or zero `dots` falls back to the boolean
`dotted` flag. Invalid flags and invalid counts remain blocking source errors.
Existing supported dot-count and notation limits still apply.

Explicit `duration` already contains the dotted duration. Neither reader
multiplies it again. The effective dot count is also used for grace allocation,
written notation and whole-rest/pickup classification. The source document is
retained unchanged. The independent verifier implements the interpretation
separately and detects changed attacks, sustains and notation in an archive.
Tempo automation's separate dotted-unit setting is unaffected.

## Validation

`tools/songsterr_compatibility/legacy-dotted-reference.cjs` generates 80 synthetic
reference cases using the reviewed, hash-pinned public player. It covers ordinary
notes, rests, tuplets, grace notes and whole rests, both field spellings and
precedence under authored and player-default profiles. The captured fixture
contains synthetic source data and observations, not player code.

`tests/test_songsterr_legacy_dotted.py` compares converter clocks and authored
events with those observations. It also checks malformed fields, leading grace
groups, whole-rest classification, exact source retention and archive corruption.

Implementation evidence is retained in the workspace under
`verification/songsterr-legacy-dotted-implementation-20261005`. Read-only replay of
the four affected app sources removes `beat.dotted` as a blocker in all four.
Midnight renders through both readers and matches the native authored clock and
events. The other three sources still contain separately unsupported legacy
fields; accepting this alias does not suppress those findings.
