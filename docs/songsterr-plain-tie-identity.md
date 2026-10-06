# Ordinary tied-note identity

Preservation contract 90 interprets an explicit Songsterr `tie: true` as a
continuation of its unique held note on the same arrangement, voice and physical
string. A valid stored fret on an otherwise plain continuation may differ from
the originating fret, including zero or a different nonzero value. It does not
request another attack or an inferred slide. Guitar Pro XML handling is unchanged.

The converter keeps the raw score and its model frets intact. Resolution is per
performed occurrence and source note identity, so repeated written bars can reach
different origins. Playable sustain and tied notation (fret and MIDI pitch) use
the same resolved origin. Onset picking/fingering and authored endpoints stay
intact; tied brush offsets do not create new attacks.

The existing continuity rules still apply: no missing origin, overlap, duplicate
same-string origin, different voice/string recovery, or explicit rest interrupting
the held note. Songsterr's already supported unfilled space may continue a tie.
Origin vibrato and a completed incoming slide are retained. Differing-fret ties
with unqualified explicit pitch/mute/harmonic gestures remain errors. Existing
same-fret expression policies and the separate dead-note identity policy remain
in place. Native player recovery across voices, rests or orphans is deliberately
excluded.

Contract 93 separately qualifies an ordinary first-origin finger bend followed
by exactly adjacent plain continuations. Its stricter whole-group boundary and
distinct evidence row rule are described in
[plain continuations of bent attacks](songsterr-bent-origin-ties.md). Ordinary
non-bent gap and expression behavior described here remains unchanged.

For each differing pitched continuation, `import/plain-tie-identity.json` records
the exact source hash, authored and effective frets, source/origin identities,
voice/string, performed occurrence and score-clock times. The manifest references
the sidecar and `songsterr-plain-tie-identity-v1` policy. Original charts, voice
projections and Hybrid Lead preserve this lineage. The archive verifier reads the
source independently and rejects missing, extra or altered evidence as well as
changed attacks, endpoints and notation. Contract 89 remains sufficient for its
separate legacy brush timing policy, but cannot publish this new interpretation.

The normalized reference fixtures derive from a hash-pinned complete public
Songsterr worker replay. They qualify preparation, authored/final rows and
synthesis events, not acoustic correctness or an interactive browser recording.
The raw worker is neither committed nor bundled. A fresh app import must still
pass ordinary acquisition, audio alignment and archive validation.
