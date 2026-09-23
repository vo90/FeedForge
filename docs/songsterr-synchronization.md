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
until it has enough boundaries. FeedForge follows this source-defined trailing
rule only with at least two supplied points, records the inference count, and
checks every playable event against the actual recording. It never fills missing
interior entries. Trailing written silence may exceed the audio; attacks and
sustains may not. See `songsterr-silent-terminal.md` for the current evidence.

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

A user-initiated **Ghost — Rats** retry on 2026-09-17 retrieved 135 synchronization
points for approved revision 7788783 and recording C_ijc7A5oAc. The local converter
log reports validated source timing and a validated FeedPak containing four
arrangements and 2,752 notes. The app then rejected the temporary file because its
logical AppData path differed from Python's resolved Windows backing path, so that
attempt did not publish a FeedPak. This is evidence of the user's live retrieval
and conversion, not a completed app import or a game-playback test. The research
tool's earlier URL-safety refusal was not retried through another route.

## Windows file locations

Windows package storage can expose one file under both an AppData alias and a
physical `LocalCache` path. Python's `Path.resolve`, Node's native `realpathSync`
and async `realpath` resolve that backing path; legacy Node `realpathSync` may
retain the alias. Songsterr file containment checks use the native resolver on
both the expected folder and the file. They still reject missing files, final
symbolic links, non-files and resolved paths outside the expected folder.

The same comparison applies to newly acquired tabs, cached tabs, staged FeedPaks
and saved-output recovery receipts. Existing logical paths in history remain
usable without moving the profile. File-location failures retain a bounded
`pathValidation` stage/reason in the local job ledger and identify the location
problem separately from converter content validation. Retrying clears that
diagnostic. Completed and failed attempts still clean their temporary files.

## Offline queue and packaged publication check

`tools/Test-SongsterrJobs.cjs` exercises the real Songsterr queue and frozen
converter with the synthetic score/audio generated by `Test-SongsterrConverter.py`.
It checks final publication, configured artist-folder/filename layout, cached
retry, collision handling, interrupted-save recovery, `outputAvailable`, and
the actual Show file IPC handler with an inert shell adapter. The output uses a
directory junction to reproduce different logical/physical path spellings. If
the selected new root already has native Windows AppData redirection, the test
uses that directly instead of adding another junction.

Run from the repository with an installed development Python containing the
existing synthetic-fixture dependencies and a **new** report folder:

```powershell
node tools/Test-SongsterrJobs.cjs `
  --python .venv/Scripts/python.exe `
  --package-root C:/path/to/build/release/win-unpacked `
  --root C:/path/to/new-verification-folder
```

The package mode extracts the Songsterr modules from `app.asar` and executes
those unchanged. For a prepackaging source check, replace `--package-root` with
`--converter C:/path/to/psarc2feedpak.exe`. The frozen child receives a system-only
PATH and no Python search path. A real redirected-location check can use a new
unique folder directly under AppData, separate from the FeedForge profile.

The harness never opens a browser, contacts Songsterr, or reads an existing user
profile. It preserves evidence and synthetic outputs in its owned directory.
It does not establish live-song synchronization accuracy or game playback.

The 2026-09-17 test reproduced the old packaged staging-path rejection and then
passed all five checks with the corrected queue, both through a directory
junction and Windows' actual AppData-to-LocalCache redirection. The latter used
the isolated `FeedForge-Path-Smoke-20260917-02` folder rather than the user's
FeedForge profile. Both conversions published two arrangements and 144 notes.
