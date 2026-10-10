# Songsterr artwork overlap

The import worker starts one album-cover lookup after `preparation.finalize`
has succeeded, including its retry without ending padding. It snapshots the
same metadata the sequential lookup used: selected metadata, converted title
and artist, album, final audio duration and kind, and the downloaded title only
when the final alignment uses Songsterr's verified video points.
If the executor cannot start a thread, its queued work is drained and the
import uses the original synchronous lookup at the join instead.

The full-recording local timing assessment runs on the import thread while
artwork resolves. Assessment only adds its report to the alignment; it does
not supply identity to artwork. Candidate order, image encoding, album
enrichment, cache identity, warning content and recording-version selection
are unchanged. Cache hits and affine alignments may offer little overlap.

The original artwork progress message remains at the join before archive
building. Progress callbacks run only on the import thread. No archive can
consume an unfinished lookup. Every exit joins the single artwork worker,
including timing failures and cooperative cancellation, before the caller can
clean up job files. Failed imports may therefore finish their optional cache
lookup before returning. Desktop cancellation still terminates the converter
process tree; the worker does not create another process.

The resolver keeps its existing request and time budgets and service rate
coordination. Waiting for its process-local rate lock now consumes the same
remaining lookup budget instead of using an unbounded lock acquisition. As
before, injected transports must respect their supplied timeout; threads are
never abandoned while they can still write.

Offline tests use event barriers for overlap, finalization retry, failure and
cancellation lifetime. A deferred sequential control and the threaded worker
produce identical provider-request order, cover bytes, builder inputs, result
metadata and warnings for cache hits/misses, ambiguity and transport failures.
No network-dependent speed guarantee follows from these tests.
