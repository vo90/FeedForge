# Collapsed opening recording bars (contract 87)

Some completed Songsterr original-recording maps repeat their first timestamp.
The public player's inverse recording clock jumps over those opening bars.
Removing duplicate boundaries would instead shift every subsequent bar, which
is incorrect. Preserve all boundaries and their original score indices.

The general policy `songsterr-collapsed-opening-v1` accepts exact equality only
in the leading prefix, with at least one positive interval following it. The
remaining points must strictly increase. Existing song/revision/video binding,
approval policy, ambiguity checks and recording-end checks remain in force.
No song names or source IDs are special-cased. No acoustic threshold changes.

Before accepting the map, check every playable note and chord member. Omit only
events wholly contained in the skipped bars. Reject crossings, linked attacks
across the boundary, mixed strum groups and removal of an entire playable track.
Do not invent a duration, move a later attack, or import zero-duration gems.
The normal minimum preparation time is calculated from the retained notes.

The exported notation omits the skipped measures but keeps source bar numbers.
Beat ordinals restart at the first retained downbeat. Later tempo changes and
recording anchors are unchanged. Lyrics wholly inside the skipped prefix are
recorded as `collapsed_opening` in the lyric ledger. Hybrid Lead excludes the
omitted source material from optional fillers and discloses opening omissions
separately from recording-end omissions.

The full source is retained byte-for-byte. `import/collapsed-opening.json`
contains the policy, source hash, score boundary, original recording timestamp
and each omitted note's source IDs, track, string, fret and original interval.
The independent source reader reconstructs these omissions and compares the
receipt and every retained chart event. A missing receipt, changed identity,
modified count, moved later note or reinserted opening note fails verification.
Imports show a notice explaining the omitted opening bars/notes.

Qualification uses the reviewed, hash-pinned public `KO` interpolation closure
through `tools/songsterr_compatibility/video-clock-reference.cjs`. Regenerate the
synthetic fixture with `collapsed-opening-fixtures.cjs common.js vendor.js NEW.json`.
Fixtures cover one/two skipped bars at multiple tempos, forward mapping and the
inverse jump. The investigation also compared the current public closure with
the pinned one and replayed the complete So Far Away recording map; source and
recording assets are local verification evidence, not shipped fixtures.

Regression tests cover source retention, chords/rests, multiple bars, Hybrid
Lead, high-fret projection, lyrics, unsafe crossings, invalid maps, receipt
tampering and ordinary negative opening brushes without this policy. This
change requires no game schema or renderer change and does not migrate old
imports. It certifies conversion against the selected source timing map; it is
not a claim that every authored note is acoustically correct.
