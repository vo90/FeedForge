# Recording timing boundaries (preservation contract 20)

Songsterr's public video player builds one score point for each performed bar
and a final endpoint. Its interpolation function uses the shorter of the score
and video arrays. Surplus video points are consequently an unused suffix, not
additional bars or an instruction to stretch the last bar.

The importer now follows that rule. It still rejects nonfinite/non-increasing
input, mismatched revision/recording identity, unverified navigation, and playable
events outside the accepted recording. Missing trailing points keep the existing
last-interval extension rule. No interior point is removed or repaired.

Every newly produced source-map alignment retains the canonical, complete input
as `sourceTiming`, with its original `mapHash` and a `boundaryPolicy` recording
supplied, used, unused-trailing, and inferred-trailing counts. FeedPaks retain
this input in `import/source-timing.json`. The independent verifier reconstructs
performed order and score coordinates from the raw tab and checks each applied
boundary, the policy, recording identity and evidence hash. Large arrays stay
out of the bounded desktop job summary.

The reference is the public `common-z7xLi0BiPF1hudTP.js` player asset captured
2026-09-23 (SHA-256
`039a95156a0b966e7c3602ea8d0f4ae403266ec2f7ba930dd64fcbe41151fc29`),
specifically score-boundary construction `Qh` and bidirectional interpolation
`gk`. Tests include extra endpoints, unchanged timing, malformed suffixes,
recording bounds, and source-evidence tampering. Following this source timing
does not certify the musical accuracy of the site's synchronization.
