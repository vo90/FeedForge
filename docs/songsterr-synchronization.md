# Songsterr recording synchronization

## Evidence and scope

Playback semantics were independently examined on **2026-09-17** in Songsterr's
public JavaScript bundle, linked from its generic [Help page](https://www.songsterr.com/help):

[common-CqwNT9PGZK_KaVsj.js](https://static3.songsterr.com/production-main/static3/latest/common-CqwNT9PGZK_KaVsj.js)

The bundle was read as text; no third-party code was executed or copied into the
implementation. Its generated names and offsets are research references, not a
stable API. Relevant locations are `so`/`Co` (performed repeat order), `wu`
(score-time boundaries), `bk` (time interpolation), and
`video/putPointsIntoPlayer` (recording-point preparation).

The API's points are **seconds in the selected recording**. The player converts
them to milliseconds and associates them with the expanded performed progression,
not merely the distinct written measures. N performed measure visits require N+1
boundaries, including the end of the last visit.

Within each interval the player interpolates between **tempo-integrated score
time** and recording time. A quarter-note fraction is equivalent only when the
score tempo is constant within that interval. Repeated visits have separate
recording boundaries even when they refer to the same written measure.

## Importer bounds

The importer binds a map to the approved song/revision and the exact YouTube video
actually downloaded. It accepts completed full-mix maps only, with finite,
strictly increasing points, consistent performed-score coordinates, and complete
playable-note coverage inside the recording. Conflicting or incompatible maps
fall back to independent audio matching. A structurally valid source map does not
claim that a human checked every note against the recording.

Songsterr's player extends incomplete maps by repeating the final known interval
until it has enough boundaries. FeedForge permits **only one missing final
boundary**, and only with at least two supplied points. It records that inference
in provenance and rejects an inferred end outside the recording. It does not
extrapolate an arbitrarily missing tail or playable events beyond its validated map.

Negative initial points can describe silent score pre-roll before recording time
zero. They are retained during validation. Negative nonplayable timeline markers
may be omitted and a tempo retained at zero; a playable note starting before the
recording is rejected, never silently clipped or shifted.

Nonnested repeats and verified single-measure alternate endings use the performed
visit order. Multi-measure ending regions are excluded from source-map alignment:
Songsterr can skip the entire region from an ending marker, whereas the current
score walker only filters measures carrying that marker explicitly.
Nested repeats are excluded from source-map alignment: the inspected player uses
one active repeat object, whereas FeedForge's score walker supports a stack.
Repeated passages with tempo changes within a measure are also excluded because
the two implementations have not established identical inherited-tempo behavior
at repeat jumps. Alternate endings without a repeat structure are not assumed
equivalent. These restrictions concern source-map alignment; they do not broaden
or weaken the independent matcher's acceptance rules.

## Validation and fallback

Source-map validation and heuristic audio matching are distinct methods. A usable
source map retimes notes, sustain endpoints, bend curves, beats, sections, and
tempo events piecewise. Its recording identity, map digest, and final-boundary
inference are recorded in the import recipe. Missing or rejected source timing
invokes the existing audio matcher with its existing confidence gates; cancellation
and unrelated failures must not become fallback attempts. If neither method
succeeds, no FeedPak is published.

Regression coverage should exercise exact recording/revision identity, map
conflicts, N/N+1 counts, monotonicity and finite values, negative pre-roll, recording
limits, repeat restrictions, variable per-measure timing, within-measure tempos,
sustains and bends crossing boundaries, and fallback/cancellation behavior. Relevant
suites include the provider synchronization and jobs tests, Python synchronization
and builder tests, and `tests/test_song_import_worker_sync.py`. Synthetic source-map
tests establish implementation behavior, not live-song timing quality.

The implemented JavaScript retrieval module was tested anonymously against
Parisienne Walkways, song 23063, revision 8981986, recording ck4ElH_wMwM. It returned
a completed map with 101 points. All nine score parts were also retrieved, each
with 100 measures. That current score has 34 staccato notes, which hit a preexisting
explicit parser limitation; this was not a completed real-song conversion test.

Live **Ghost — Rats** source-map verification remains unresolved in this work.
A prior research tool's URL-safety refusal for its revision page was not retried
through another tool or route. The earlier score/audio acquisition and independent
matcher result do not establish that this new source-map path works for Rats. No
live Rats source-map success or game-playback claim is implied.
