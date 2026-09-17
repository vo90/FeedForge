# Songsterr import (experimental)

Find songs now has CustomsForge and Songsterr sources. Songsterr searches publicly,
resolves an explicitly approved revision, and first attempts to retrieve every track
without account credentials. If this fails, the activity card can offer account
export. The user signs in through Songsterr's own isolated browser, then continues;
FeedForge creates an unpublished copy and exports its Guitar Pro file. A durable
journal prevents another Create click when the previous result is uncertain.

Routine acquisition runs in hidden, sandboxed windows. Login and website checks can
be opened explicitly. No account cookies are shared with CustomsForge or extracted
from another browser. FeedForge never publishes a Songsterr copy.

The imported score retains the original artist/title, all supported guitar/bass
tracks and their physical strings, tuning, capo, repeats, positional tempos and
supported techniques. Drums/vocals and other excluded tracks are recorded in source
coverage. The converter creates original full audio, an Ogg preview, a generated
title cover and a validated FeedPak. It does not perform stem separation.

When no usable original audio link is found, choose a local audio file or paste an
audio/YouTube link in the activity card. This never substitutes Songsterr's
synthesized playback for the original recording. Original audio availability and
YouTube retrieval can vary; failure offers the local-file route.

Output folder, naming template and artist/flat folder layout come from FeedForge
Settings. A network import has no source folder, so Preserve uses the selected
output folder. `{source}` uses `Artist - Title`, without provider IDs or copy-title
prefixes. Settings are captured when a job is queued. Existing files are never
overwritten. Reimporting identical source/audio/settings reuses an unchanged saved
output; changed inputs receive the normal collision suffix.

## Implementation boundaries

- `electron/song-browser/providers/songsterr`: search, approved revision resolution,
  separate browser sessions, anonymous complete-score retrieval and account export.
- `songsterr-service.cjs` and `songsterr-jobs.cjs`: trusted IPC, source registry,
  serial queue, cancellation, recovery receipts and audio prompts.
- `publication.cjs`: shared CustomsForge/Songsterr exclusive, hash-checked publication.
- `src/feedback_converter/song_import`: independently implemented MIT score parsers,
  performed timeline, audio preparation, alignment and FeedPak builder. No FeedBack
  AGPL implementation was copied. A running game is not needed.
- `--song-import-file request.json`: converter entry point. It writes staging files
  only; the desktop app publishes them after another validation pass.
- `--song-import-health`: offline dependency check for local and frozen converters.

Original source IDs, approved revision, source/audio hashes, parser coverage and
alignment recipe are retained as provenance, not exposed in the output filename.
Source scores remain in the private queue cache for pending imports and the ten
most recent finished jobs. History keeps 100 jobs. Large working audio and staging
directories are removed after each attempt. Retrying an evicted score retrieves it
again and checks approval anew.

## Current limits and evidence

This is an experimental single-song implementation, not a production coverage claim.
Anonymous acquisition and website controls are exercised with fixtures; no successful
live anonymous retrieval has been established. The earlier research tool's URL
safety denial was not retried through another tool or this implementation.

The first live Ghost — Rats attempt stopped before acquisition because the revision
button was not recognized. Its public revision UI was inspected on 2026-09-17:
`#revisions-toggle-tab` opens `#revisions-list`, with approved revision `7788783`
dated July 8, 2026. Regression fixtures cover that button, individual revision rows
and delayed rendering. This verifies the revision-resolution fix, not a completed
live Rats import.

The real approved Woodland Rites GP8 export was parsed offline: all four guitar/bass
tracks, 168 performed measures after repeat expansion, 272 seconds at the score's
tempo. Original Songsterr IDs and metadata are preserved separately from its copy.

Automatic alignment currently fits a global offset and constant tempo ratio, with
held-out pitch, attack, regional drift, coverage and ambiguity checks. Confidence
is an engineering gate, not a calibrated probability. Wrong recordings, uncertain
matches and nonlinear drift are rejected. A user may supply a better matching
recording; there is no manual timing editor in this flow. Nonlinear alignment and
broader real-song calibration remain future work.

Unsupported critical notation fails explicitly rather than silently dropping it:
navigation jumps, swing, linear tempo ramps and some ornaments. Supported output
string counts are guitar 6/7/8 and bass 4/5/6. Raw Songsterr JSON semantics still need
validation against captured real approved scores. Songsterr batch import is deferred.

## Build and verification

Install the project and its dev dependencies in an isolated Python environment,
then run `npm ci` and `npm run build`. Python dependencies include NumPy,
soundfile, yt-dlp and its matching EJS package.

Portable builds require explicit FFmpeg, FFprobe and Node (22 or newer), plus the
existing PSARC decoder tools. Supply these through `FEEDFORGE_PACKAGE_AUDIO_TOOLS`
for the normal spec or `-AudioToolsPath` for `tools/Build-SongBrowser.ps1`. Place
their notices in that directory's `licenses` folder. The specs bundle the tools,
Python modules, yt-dlp/EJS data and notices. The converter prefers these bundled
tools; a frozen build does not search the user's PATH for them.

Useful checks:

```text
node --test test-song-browser/*.test.cjs
python -m pytest tests -q
python -m feedback_converter.cli --song-import-health
python tools/Test-SongsterrConverter.py --converter <frozen-converter> --root <new-test-folder>
```

The tests cover approved revision pinning, account-copy ambiguity, metadata,
repeats/endings/tempos/techniques/strings, missing/incorrect audio, synthetic full
conversion, output settings, cancellation, interrupted commit recovery, path
containment and duplicate publication. The portable smoke generates its own score
and recording and uses a restricted PATH; it performs no live site requests.

Live Songsterr search/export, anonymous score availability, linked YouTube audio
and playback in FeedBack still require separate end-to-end acceptance. Those checks
must not be inferred from fixture tests or schema validation.
