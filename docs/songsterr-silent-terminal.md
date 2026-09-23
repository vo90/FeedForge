# Silent terminal synchronization boundary

The public last-interval rule can place an inferred final measure boundary
after the recording ends. This is permitted only when the last supplied
boundary is inside the recording and every playable attack and sustain passes
the existing audio-bound checks. Explicit out-of-range boundaries, missing
interior points, ambiguous maps and actual notes outside the recording still
fail. No map point, note, bend or sustain is moved or clipped.

The full inferred measure remains in written notation. The playback beat/
tempo grid ends with the recording, matching the existing builder behavior.
Independent verification applies that grid boundary only for the explicitly
marked inferred silent terminal case, and still verifies every written beat
and playable note.

Frozen 2026-09-23 reproductions:

| Song | Recording end | Inferred terminal | Last actual note end | Consequence |
|---|---:|---:|---:|---|
| Last Resort | 198.8673 s | 200.0600 s | 196.9213 s | Eligible for unchanged map |
| Wake Me Up When September Ends | 286.0002 s | 288.2400 s | 283.8000 s | Still rejected; the last supplied boundary is also beyond the recording |
| Every Breath You Take | 228.8907 s | 230.5800 s | 229.3931 s | Still rejected; two notes exceed recording |

Chop Suey! and Sonne had conflicting captured map candidates. Wonderwall had
92 points for 94 performed measures. Those are not solved by a terminal
boundary change. Confidence thresholds and alternative-map selection remain
unchanged. These are alignment-stage results, not completed-package claims.

The packaged frozen-source benchmark confirmed that Last Resort completes.
Wake Me Up When September Ends still fails the explicit-boundary guard; the
silent inferred-terminal exception alone does not cover that source map.
