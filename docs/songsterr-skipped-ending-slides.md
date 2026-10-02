# Slides orphaned by a skipped ending

Preservation contract 55 omits an unresolved outgoing shift or legato slide
only on the affected performed occurrence. It preserves the note, attack,
duration, other effects, and correctly resolved slides on other passes.

The qualification is structural, with no song identifiers or time thresholds:

1. The source segment ends exactly at the written measure boundary.
2. The next written bar starts an alternate ending, with one pitched,
   non-tied destination on the same source voice and string at its downbeat.
3. This performed pass skips that ending with a forward traversal jump.
4. An explicit rest in that voice precedes any subsequent event on the string.

The skipped destination is evidence of a lost connection, not a fret to play
on this pass. Neither a distant later note nor another string in the chord
supplies a replacement pitch or direction. An unfilled bar is not an explicit
rest. A closer attack or tie takes precedence. Existing guards for unqualified
repeat links, missing targets, muted shifts and HO/PO remain active. A valid
destination in the performed ending continues to resolve normally.

This policy is independent of source-to-recording alignment. The complete
source stays unchanged. Written notation retains its authored marking; the
performed highway omits only the unresolvable connection. Compatibility finding
`note.slide_skipped_ending` explains the display limitation. The version-2
`import/undefined-slides.json` receipt records the origin, performed occurrence,
skipped target, actual traversal transition and interrupting rest in score time.
It also contains any existing slide-to-mute omissions. Archives containing only
the contract-54 mute rule continue to use the version-1 receipt.

The verifier independently derives the omissions from its raw-source parser,
repeat expansion and rational clock. It traverses voice events in reverse;
the producer indexes forward events and rests. Neither shares the other's
detector. The verifier checks the note data, receipt and compatibility report,
including rejection of an invented slide, missing note, modified good pass,
wrong occurrence/rest, or downgraded contract.

Reference investigation confirmed that the captured Songsterr synthesizer can
borrow the next same-string fret across rests and repeats, even many bars later,
to synthesize a short slide inside the original note. This is retained as
development evidence, not copied as an instruction to play that distant fret.

Validation uses synthetic alternate endings, independent package-corruption
checks and the retained source corpus. Source conversion success alone does
not establish successful recording acquisition or audio synchronization.
