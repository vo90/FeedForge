# Finger bends alongside explicit bar curves

Contract 78 / finger-bend evidence 18 qualifies the finger-bend clock independently
of an explicit whammy-bar controller. The source player schedules these controls
separately; it does not establish a new additive pitch curve.

The scope is one continuous same-fret/string/voice held note, with no competing
finger-bend intervals, authored attack displacement, slide, legato, harmonic,
mute, staccato or other unqualified expression. Initial and later tied bar
curves, held and flat controls, and their qualitative bar vibrato are covered.
Ghost markings and ordinary note vibrato retain their existing meaning.

Only the finger-bend curve (`bnv`/`bn`) is rebuilt on the existing source tie and
tempo clock. Attacks, sustains, bar curves and resets, source identities, chord
membership and all other fields remain unchanged. The optional-whammy policy
and game limitation for combined bar/finger pitch remain in force. No combined
pitch scoring or renderer change is part of this fix.

`barCurve` evidence records the policy, source IDs, repeat occurrences, score
intervals and rational source control positions. The independent rational
verifier reconstructs both eligibility and evidence from the preserved source;
it does not trust the exported evidence or call the producer helper. Older
contracts cannot claim this qualified conversion. Existing FeedPaks are not
rewritten; reimport uses the new contract.

The synthetic reference fixture covers guitar/bass, dip/held/flat/precise curves,
initial/later tied controls, hold/release, repeat, tempo and chord contexts. It
records comparisons against the pinned public Songsterr worker
`FluidsynthAudioPlayerWorkerEntry-C-9Kd3shWgzk98Al.js` (SHA-256
`4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab`).
Finger-handler events and timing were unchanged when bar controls were removed
under both authored and player-default profiles. Constant-tempo samples also
check actual exported curves against the native staircase within its MIDI and
step quantization tolerance. This is control-timing evidence, not a claim that
the two pitch controls sound additive.

Tests additionally check deferred compositions, source preservation, offset and
piecewise alignment, Hybrid Lead and rejection of mutated bends, bar controls,
attacks, source evidence and contract versions.
