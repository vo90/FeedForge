# Outgoing HO/PO consumed by a tied entry

Preservation contract 95 qualifies an outgoing Songsterr `hp: true` whose next
same-voice, same-string entry is an immediate literal `tie: true`. The unique
plain tied entry consumes the instruction; it continues the existing held
target and creates no attack. The next fresh note does not inherit this consumed
HO/PO cue. Equal and differing stored tie frets follow the same rule.

The complete held history must be unique, exactly continuous and uninterrupted
by rests. Missing origins, duplicate source slots, overlap, gaps, unsafe repeat
jumps, competing slides and unqualified pitch/controller combinations remain
errors. Initial explicit note vibrato and already resolved incoming HO/PO are
preserved. Existing independently qualified later tie expressions are unchanged.
Only the unused outgoing `ln` cue is removed; genuine attack links remain.
Guitar Pro import handling is unchanged.

`import/pending-hopo-ties.json` binds the consumed marker and immediate tie to
the original source hash, held attack, performed occurrences, voice/string,
score-clock boundaries and authored/effective frets. The manifest selects
`songsterr-pending-hopo-immediate-plain-tie-v1`. The compatibility inventory
discloses that hidden synthesis articulation is retained as source evidence;
it does not invent a playable pitch, extra attack or delayed technique.
Electric/bass synthesis triggers are not presented as a new game gesture.

The independent reader reconstructs this rule without importing the producer
predicate. It checks playable targets, attack/end clocks, notation, receipt and
compatibility accounting. Contract 94 and earlier archives retain their former
HO/PO interpretation. Current semantics cannot be published under those older
contracts or without the original score and current inventory.

Qualification uses the reviewed hash-pinned Songsterr native playback evidence
and synthetic boundary regressions, plus unchanged-source real-tab contexts.
This rule does not repair tab timing, relax audio alignment or resolve unrelated
slide/bend/tie guards. No game renderer or scoring extension is needed.
