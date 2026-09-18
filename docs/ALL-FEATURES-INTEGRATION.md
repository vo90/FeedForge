# FeedForge all-features integration

This branch consolidates the user's FeedForge development before the separate
upstream v2 integration. It is a preservation baseline, not an upstream release.

## Sources

| Development line | Pinned source | Treatment |
| --- | --- | --- |
| Latest conversion integration | `bb1e69c5c2f2715a6c59a58bd0711c0d8bc5ac5a` | Foundation; includes the three commits beyond local `dev/conversion-fidelity`. |
| Latest Find Songs / Songsterr chain | `68a957532c561d102c261ff911455b346d9e7aec` | Merged with history, including CustomsForge expansion, source-preserving Songsterr import, timed outgoing and incoming slides. |
| Library review | `1c4ed08510698e8adb7e3c5489078246ad54b8ca` | Missing review features adapted in `4aea88a`; modern scheduling and guarded deletion retained. |
| Older conversion investigation | `57f51f31c17bdd8d51db425f78a9f3e841d0847f` | Residual Unicode naming, scoped local artwork URLs and diagnostic redaction restored through separate feature commits. |

The original branches remain available. Older branch tips were compared by
behavior, not merely by ancestry: shared timeline, semantic validation and
technique test branches include equivalent patches already present in the
conversion foundation. The older RS1 worker, archive correctness, batch naming,
preview fallback and warning branches are already represented or superseded,
except Unicode filename preservation, which is restored here.

## Retained behavior

- PSARC conversion fidelity: downbeats, per-string chord sustain, high-density
  chords, authored arpeggios, arrangement identity and tuning offsets, single-level
  phrases, muted fret handling, bend timing and authored bend targets.
- Exact embedded XML recovery, structured audio indexing, scoped multi-song
  metadata, staged publication, validation policy and partial-success reporting.
- Machine-aware worker recommendations and manual limits, weighted process
  admission, memory protection, inspection and multi-song worker controls.
- CustomsForge search, sorting, filters including any/all selected arrangements,
  ODLC labels, host handling including MEGA, file selection, batch review,
  duplicates, persistent jobs and output reuse.
- Songsterr search, approved-revision identity, recording alignment, independent
  source verification, notation/evidence, compact cover artwork and preview audio.
- Source-preserving ghost notes and direction-only incoming/outgoing slide data.
- One Settings source for output folder, naming template and folder layout.
- Library inspection and editing, optional stem tools, searchable audit results,
  result filters and pagination.

## Deliberate integration decisions

The latest PSARC converter and worker policy are retained. The Songsterr modules
remain separate; no upstream v2 conversion engine is introduced.

Process environment resolution combines verified decoder/DLL paths, the selected
development source tree and per-import temporary folders. It retains process
group cancellation and never changes global environment settings.

Song imports can be cancelled while waiting for the shared worker limit or
converter identity. Shutdown uses one completion barrier for song jobs, provider
cleanup, converter processes and the stem service, including when a download is
active before any converter has started. Repeated quit requests share that cleanup.

Library duplicate checking keeps its existing disabled default and strict
artist/title/album/year/duration matching. Broader artist/title matching is an
explicit review option; different releases are not silently equated or deleted.
Existing saved criteria without a matching mode retain strict behavior.

Filename sanitation preserves Unicode using NFC while making Windows reserved
names safe and removing invalid separators/control characters and unsafe trailing
characters. Existing converted files are not renamed by this change.

Artwork and tone images use opaque local URLs restricted to registered roots.
Native file paths remain available for native operations. Diagnostic logging
redacts the stem service API key without altering the arguments passed to it.

The older standalone decoder installer is superseded by the newer decoder
discovery/packaging implementation. The original source-path fix is retained in
the combined process environment. Diagnosis-only CI branches are not additional
product features to merge.

## Verification commands

With dependencies already prepared for this checkout:

```text
npm test
npm run test:python
```

These include worker, library, desktop integration, CustomsForge and Songsterr
tests. The Python command includes the instrument-evidence suite outside the
main tests directory. Cross-repository consumer checks can skip when their
external fixtures are not configured; those skips are not game-rendering proof.

The isolated portable Windows builder supports this branch and an explicit
existing dependency checkout using `-DependenciesRoot`. It requires a matching
lockfile and installed package versions, produces output outside source, and
does not install dependencies. See [the build instructions](../tools/SONG-BROWSER-BUILD.md).

The real-file acceptance checks rebuild cached Rats using both this integration
and the pinned latest Songsterr branch, independently verify the result against
the source, and compare every archive member. A separate cached PSARC check runs
inspection, conversion, validation, output reuse and cross-folder publication.
All acceptance output belongs to disposable verification directories, not a
personal song library. Live provider downloads and FeedBack rendering remain
distinct checks from these offline converter tests.

## Repository boundary

This baseline is FeedForge only. FeedBack's ghost-note/slide rendering and its
other game features belong to their own repositories. This branch preserves the
associated FeedPak data and does not merge or modify those game repositories.

Upstream v2 integration is intentionally a subsequent stage, starting from this
tested branch so that later replacement decisions can be reviewed independently.
