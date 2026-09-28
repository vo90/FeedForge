# Recovering unavailable Songsterr full mixes

The site's Original / Full mix player can replace an unavailable upload with
another full-song upload. Such uploads can have `feature: alternative` in the
timing API; this is different from backing, solo and playthrough mixes.

Jobs now record whether the site or user selected their audio. Only a narrowly
classified YouTube `Video unavailable` download failure on site-selected audio
can automatically recheck the ordinary player for the same approved revision.
Private, sign-in, age restriction, challenge, unknown and alignment failures do
not qualify. Recovery uses the queue's existing two shared retry credits, with
its existing cancellation, persistence and cooldown behavior.

Discovery excludes failed video IDs and waits for the ordinary player fallback.
It does not search YouTube or substitute a file from a list. A replacement must
have its own exact song/revision/video timing map. The bounded API response is
reselected independently to reject backing/solo/playthrough, track-scoped,
unfinished, problematic, conflicting or identity-mismatched maps. Existing
converter alignment and independent publication verification still apply.

The tab bytes, approved revision and output settings stay fixed. Previous
recording-dependent results are invalidated. Both video IDs and the reason for
selection remain in bounded attempt history; timing-selection evidence remains
in the conversion report. Failed candidates do not publish output.

Older jobs have unknown audio provenance and cannot silently switch recordings.
An explicit **Check Songsterr full mix** action is available when awaiting audio.
**Retry same recording** retains the current selection. A pasted link or selected
file always takes priority and disables automatic substitution. If recovery
cannot find and verify another full mix, the job asks for audio as before.

Validation covers changed and unchanged candidates, unsuitable/mismatched maps,
shared retry exhaustion, restart, cancellation during discovery, user-selected
audio and explicit recovery of legacy jobs. No song-specific IDs or exceptions
are part of this behavior.
