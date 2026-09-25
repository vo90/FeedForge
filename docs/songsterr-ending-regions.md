# Repeat ending regions, preservation contract 19

An ending marker starts a pass-owned region through the next ending or repeat
close. Unmarked continuation bars belong to that region. Skipping the closing
bar does not skip the repeat's control flow. A final ending immediately after
the close and its following bars are played once. Source measure indices,
occurrence counts and written notation remain available.

The production iterative walker uses masks compiled from those regions. The
independent verifier partitions intervals and expands them recursively. A
missing pass, overlapping pass ownership, unresolved outside ending, unclosed
repeat, ambiguous implicit prefix or nested ending remains rejected. Ordinary
nested repeats retain their previous behavior; direct recording synchronization
still rejects them and repeats with within-bar tempo changes.

Multi-bar endings may use an exact-revision, original-video timing map only
after independently re-reading retained source and matching every written bar,
visit, quarter coordinate and score timestamp. A producer flag or matching point
count alone is insufficient. Existing identity, coverage, audio-boundary and
ambiguous-map safeguards remain in place.

Packaged tempo events omit only redundant consecutive equal effective tempos.
Returns to earlier tempos and within-bar changes are retained, as are raw source
annotations. Tests include hand-calculated two/three-pass orders, shared endings,
implicit starts, separate repeats, malformed navigation, real tempo restoration,
source-sync corruption and a complete archive with deliberately mistimed notes.
