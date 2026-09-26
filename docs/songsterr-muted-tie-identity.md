# Muted tie identity (preservation contract 30)

An explicitly dead note may omit its fret. The parser represents that with the
existing unpitched-mute sentinel 127. Some tied continuations instead explicitly
carry zero while remaining dead notes. A tie does not establish a new attack.

The importer accepts this absent/zero identity difference only for contiguous
same-voice/string dead-note ties with a valid predecessor, no intervening rest,
and no pitch-specific gesture. It retains the attack's target and authored tied
end. It does not replace meaningful nonzero frets, repair orphaned ties, join
voices, convert a mute to an open pitch, or invent a new technique or attack.
Existing pitched-origin/late-dead and muted-slide policies remain separate.

`import/muted-tie-identity.json` records the unchanged source hash, source and
origin IDs, locations, score-time intervals, authored/used target and rule.
The independent verifier reconstructs this evidence from unchanged raw source
without calling the producer's normalization. Missing/altered evidence or altered
notes fail verification. Voice splitting preserves the correct evidence owner.

Original source bytes remain embedded. Where a source contains an unpitched mute,
staff notation remains retained in source evidence under the existing limitation;
the playable chart/tab keeps the muted string and timing without inventing MIDI
pitch. This mapping itself is not a dropped technique or note omission.

Contract 30 separates current conversion/cache identity from historical results.
No song title, artist, source ID, revision or measure number selects the rule.
