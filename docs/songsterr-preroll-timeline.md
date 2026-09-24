# Silent pre-roll timeline metadata (preservation contract 12)

An exact Songsterr synchronization map can place silent opening bars before
the recording starts. Removing their negative-time beat markers used to leave
the first exported downbeat numbered 2 or higher, which failed FeedPak
validation. The time signature active at the recording start could also be lost.

The exporter now numbers the retained downbeats consecutively from 1. Pickup
beats stay unnumbered and every retained timestamp stays unchanged. It carries
the last pre-recording time signature forward to time zero unless a new meter
already starts there. Earlier meter changes do not create competing entries at
zero.

This changes timeline metadata only. Original source bytes, written measure
numbers, note attacks, durations and source synchronization are preserved. The
existing guard still rejects a map that places a playable attack before audio.
Original-audio selection, manual-audio fallback and recording-end policies are
unchanged; this does not enable alternative recording selection.

The independent verifier derives visible downbeat ordinals and the active meter
directly from source bars. Contract 12 is required for new publication, reuse
and resolving old worklist findings. Historical reports remain readable.

Regression coverage exercises a complete import with no pre-roll, a partial
opening bar, several silent bars, meter changes and a meter change exactly at
zero. Deliberately corrupted numbering, meter and note timing must fail
verification. No FeedBack or optional-plugin changes are required.
