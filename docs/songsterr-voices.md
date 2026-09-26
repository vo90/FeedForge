# Songsterr voice arrangements

Conflicting instructions across authored voices become separate full-song
arrangements named after the source track with a `Voice N` suffix. Each uses the
same recording, tuning, timeline and original voice number. Silence in a voice
stays silence. No voice is ranked, discarded, transposed or moved to another string.

Ordinary polyphony remains together. At the same performed onset and string,
equivalent attacks are combined after resolving ties and techniques. A shorter
explicit let-ring note may share the longer identical attack's duration only
without contradictory effects or an intervening attack on that string. Other
conflicts, including different-pitch overlapping sustains across voices, split
the whole source track. Invalid duplicates inside a single voice still fail.

Combined attacks keep every contributing source location. Combined simultaneous
chords use their union of strings without inventing a chord name. The complete
original tab, including written voices and authored labels, stays in the archive.
Existing approved high-fret omission and recording endpoint rules apply after
voice projection; projection does not bypass other conversion checks.

Preservation contract 27 stores `import/voices.json`, referenced by
`song_import.voicesFile`. The source hash, stable derived IDs, voice membership,
merge rule and score-time boundaries are independently reconstructed during
archive verification. Derived arrangements retain only their own notation and
technique sidecars. TabView uses this mapping for source-bound strum groups.
Imports made with older preservation contracts retain their original verification
semantics. Import again to apply the new policy to an existing song.

Regression coverage includes duplicate and conflicting attacks, let-ring limits,
chord unions, repeats/ties, strums, piecewise recording alignment, notation limits,
high-fret omissions and altered archive evidence. The frozen corpus replay and
shared-runtime acceptance reports live in the workspace verification directory.
