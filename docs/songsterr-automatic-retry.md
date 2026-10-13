# Automatic recovery in Find songs

Songsterr imports allow two automatic recovery attempts after the first attempt.
The queue owns this shared budget across tab retrieval, original-audio discovery,
timing-map retrieval and audio download. Ordinary delays are 5 and 20 seconds,
plus up to one second of jitter. A valid Retry-After deadline takes precedence;
waits over five minutes park the failing job for manual action. Service cooldowns
also gate newly queued jobs, including a recording discovered after admission.

Only versioned, allowlisted transport facts trigger automatic recovery. Missing
audio, private videos, sign-in, challenges, changed/corrupt sources, unsupported
notation, alignment rejection, local IO, processing and unknown failures remain
manual. The narrow YouTube media-download 403 signature re-extracts transport
URLs for the selected recording; an arbitrary 403 is not enough.

An exact YouTube `Video unavailable` failure may instead recheck Songsterr's
Full mix player, only for a recording the site selected. It shares these two
recovery credits, excludes already failed videos and requires a verified timing
map for the replacement. See [full-mix recovery](songsterr-full-mix-recovery.md).

The approved revision is saved before audio discovery, then the acquired tab and
hash are reused. Output settings, verification and publication checks are
unchanged. User-supplied audio is never automatically replaced. No account copy is recreated after an
uncertain creation response. Managed downloader retries are zero at yt-dlp's
download/fragment/extractor/file-access layers so they cannot multiply the budget.

The 30-song queue limit counts running, queued and automatic-retry work. Songs
waiting for audio, sign-in, alignment input, attention or a Hybrid Lead choice
do not consume queue slots. Manual resumes use the same admission limit before
changing the saved attempt. History keeps up to 100 entries by retiring terminal
entries first; it never silently drops unfinished imports. At 100 unfinished
imports, adding another requires resuming or cancelling one. Older oversized
ledgers retain all unfinished entries until the user resolves them.

Waiting search results offer **Resume existing import**, which focuses the saved
attempt and its input controls, and **Start fresh import**, which acquires a new
tab using current output settings and the chosen revision policy. Automatic
selection is resolved again; an explicit revision stays explicit. The old attempt
and evidence remain intact. Repeated fresh clicks use a persisted origin ID to
avoid duplicate attempts, and active imports still prevent duplicate work.

`retry_wait` releases the worker. Cancellation invalidates pending work, and app
shutdown clears its timer. Startup recovers verified publication receipts before
resuming valid waiting retries. Old failures and interrupted active imports do
not automatically restart. Persistence failure stops dispatch until reopening.

History stores small bounded cycle records under `retry-history`, separately
from disposable media folders. The ledger retains the current cycle and up to
20 previous cycle references; older records remain on disk. Exported reports
wrap the unchanged canonical conversion report with `attempt-history.json`.
No recording cache or expanded cleanup policy is introduced.

Each actual audio-discovery probe now saves one `audio_discovery` event in that
job's attempt history before acquisition or conversion continues. Its
`audioDiscovery` field records version 1, terminal outcome/reason, start/end
timestamps and at most 16 allowlisted control/action events. A `truncated` flag
marks omitted events; the terminal reason is stored separately. A concrete
failure reason survives generic fallback and player-pause cleanup diagnostics.
Cancellation and thrown probe failures are recorded too.

The summary is operation-scoped: a new song or a retry cannot borrow another
job's browser trace. Initial discovery, cached-score audio detection and full-mix
recovery use the same callback. Jobs that reuse known or user-supplied audio do
not invent a discovery event. The existing bounded app-session trace remains a
debugging aid; durable evidence is in `attempt-history.json` in the job's report.
Older jobs without this evidence remain valid; past reasons cannot be recreated.

Summaries contain only fixed enums and numeric timestamps, never page text,
URLs, credentials, raw errors or account details. They are sanitized before
saving and again during restoration/report export. Saving uses the existing
history failure handling, so disk or access failures pause imports rather than
silently losing the attempt. This does not add player warnings, automatic
retries, recording fallbacks or changes to converter verification.

Regression tests use an injected queue clock, provider failures and both
downloader routes. Packaged tests inject faults from an external test harness;
there is no production fault-injection switch.
