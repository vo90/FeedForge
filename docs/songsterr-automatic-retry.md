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

`retry_wait` releases the worker. Cancellation invalidates pending work, and app
shutdown clears its timer. Startup recovers verified publication receipts before
resuming valid waiting retries. Old failures and interrupted active imports do
not automatically restart. Persistence failure stops dispatch until reopening.

History stores small bounded cycle records under `retry-history`, separately
from disposable media folders. The ledger retains the current cycle and up to
20 previous cycle references; older records remain on disk. Exported reports
wrap the unchanged canonical conversion report with `attempt-history.json`.
No recording cache or expanded cleanup policy is introduced.

Regression tests use an injected queue clock, provider failures and both
downloader routes. Packaged tests inject faults from an external test harness;
there is no production fault-injection switch.
