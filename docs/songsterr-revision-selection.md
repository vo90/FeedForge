# Songsterr revision selection

Automatic imports prefer the newest completed positive review or the current,
verified moderator correction. If neither exists, they choose the newest eligible
unreviewed revision, including revisions awaiting moderation or review. A fresh
import reads history again, so a newer eligible upload replaces the previous
fallback. An existing job or retry keeps its original revision.

The optional **Choose revision** control can select another eligible revision,
including a pending revision newer than an approved one. Selection is checked
again in the main process when the job runs. Renderer input contains only a song
ID and optional revision ID; it cannot supply moderation evidence.

## Observed Songsterr states

Verified against Songsterr's public history, metadata and label renderer on
2026-10-03. These endpoints are undocumented; unknown states fail closed.

| History state | Interpretation | Import policy |
|---|---|---|
| Positive `reviewed.conclusion`: `approved`, `fair`, `average`, `good`, `excellent` | Approved | Preferred automatically; explicitly selectable |
| No review, `isOnModeration=false`, no block/deletion | Unreviewed | Newest fallback; explicitly selectable |
| `isOnModeration=true`, `moderationType=pre` | On moderation | Newest fallback; explicitly selectable |
| `isOnModeration=true`, `moderationType=post` | On review | Newest fallback; explicitly selectable |
| `isBlocked=true` with a completed positive review | Alternative/non-main | Explicit selection only |
| `isBlocked=true` without a verifiable positive review | Unexplained or negative alternative | Unavailable |
| Rejected conclusion (including renderer aliases `-` and `🙅‍♂️`) | Rejected, sometimes displayed as Alternative | Unavailable |
| `isDeleted=true` | Deleted | Unavailable |
| Missing required flags, unknown values, conflicting evidence | Unverified | Unavailable |

Legacy history sometimes omits `moderationType` or `reviewed`; absence alone is
not rejection. However, a pending revision must identify its moderation workflow.
AI/GP origin markers do not establish eligibility or disqualification. Moderator
status alone does not establish approval: the existing moderator exception still
requires agreement between the current published default, latest ID, author,
history and rendered moderator marker.

References:

- [Songsterr label implementation](https://static3.songsterr.com/production-main/static3/latest/common-Cjz0mibBPvAFe8mS.js)
- [Black Magick Radio](https://www.songsterr.com/a/wsa/green-lung-black-magick-radio-tab-s6472417),
  [public history](https://www.songsterr.com/api/meta/6472417/revisions),
  [exact revision metadata](https://www.songsterr.com/api/meta/6472417/8451622)
- [Stairway to Heaven history](https://www.songsterr.com/api/meta/27/revisions)
- [Rats history](https://www.songsterr.com/api/meta/441770/revisions)

## Import integrity

- Read the complete rendered history, including **Show more**, and cross-check
  eligible entries against the public metadata/history response. Incomplete or
  conflicting history does not trigger a fallback. Requests and pagination remain
  bounded and cancellable.
- Sort by creation timestamp, breaking equal timestamps by numeric revision ID.
- Store version 2 selection evidence: song/revision, selection mode, status basis,
  moderation flags, review conclusion, author, creation time and check time.
  Keep `approval=unreviewed` for pending/unreviewed tabs; never manufacture approval.
- Pin score parts, account-export source, audio discovery and recording timing
  to that exact revision. Retain the evidence across retries, restart, original-only
  fallback, conversion recipes and exported evidence reports.
- Recheck public eligibility before conversion and immediately before saving or
  reusing an output. Deletion, rejection, an unverifiable state or a failed lookup
  prevents saving. A status change never silently selects another revision.
- Keep all existing track completeness, notation, source preservation, audio
  matching and timing checks. Eligibility makes a tab available for conversion;
  it does not certify transcription quality or bypass converter checks.
- Existing approved jobs remain compatible. New unreviewed/alternative jobs require
  a validated receipt in both JavaScript and Python. Both validators run the same
  contract fixtures in `tests/fixtures/songsterr-revision-selection.json`.

## Validation

Automated coverage includes every recognized state, negative/unknown evidence,
newer pending uploads, reviewed-first preference, explicit selection, pagination,
ties, exact metadata, IPC ownership, queue deduplication, restart/retry pinning,
rejection during conversion, truthful UI status, report provenance and timing
validation. Commands:

```text
node --test test-song-browser/*.test.cjs
python -m pytest tests/test_song_import_revision_policy.py tests/test_song_import_synchronization.py tests/test_song_import_score.py tests/test_song_import_evidence.py
```

The broader `tests/test_song_import*.py` run passed 807 tests initially, with
three environment failures and two skips. Pointing subprocesses at this branch's
`src` and making the existing yt-dlp installation available resolved all three;
the additional pending-revision end-to-end case also passed (811 passing cases
across the suite and focused follow-up). No dependencies were installed. The
initial default pytest temporary directory was inaccessible; subsequent tests
used freshly allocated temporary directories.

The live public check on 2026-10-03 selected Black Magick Radio revision `8451622`
as unreviewed, acquired all five source tracks and 167 measures, rechecked its
eligibility, retrieved its 167-point timing map, and parsed the score while
preserving its unreviewed receipt. Rats confirmed the 40-row initial history and
**Show more** expansion to 42 rows. The chooser was also exercised interactively
with synthetic approved, pending and rejected rows. These checks did not publish
to a song library or perform full recording/gameplay acceptance.
