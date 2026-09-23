# Recorded final-sustain adjustments (contract 10)

Contract 11 adds a separately guarded final-bar attack cutoff, described in
`songsterr-recording-end.md`. The held-tail behavior below remains unchanged.

A recording may contain every attack and pitch gesture while ending before the
last held notes finish. With an accepted Songsterr timing map bound to the exact
song, approved revision and selected recording, FeedForge shortens those held
tails to the decoded audio endpoint. It never changes the audio or selects an
alternative recording to make the import pass.

Every attack must precede the recording end, including attacks in other playable
arrangements. Changing bends, target-fret slides, and slide events crossing the
cut still reject the map. A completed bend may keep a shorter constant-pitch tail;
only redundant held-value curve endpoints can move to the cut. Continuous flags
such as vibrato, mute, accent and ghost are retained. The independent audio
matcher's existing rules are unchanged; it cannot enable this exception.

The original source bytes and written notation are retained. The FeedPak embeds
`import/sustain-adjustments.json` with the policy, exact audio duration, track,
string, fret, attack time, original mapped duration, exported duration and amount
shortened for each affected string-note (including chord members and ties).
FeedForge shows the adjustment count with the completed import.

Preservation contract 10 independently reconstructs allowed changes from the raw
source, compares every ledger entry and exported note, and checks the packaged
audio's real duration. A missing, false or undeclared adjustment fails verification.
Compatibility, evidence, verification and publication gates advance together.
Historical contracts 1–9 stay readable but cannot satisfy the current import gate.

Tests cover plain notes, ties, chords, completed bends, unchanged flags, late
attacks, unfinished gestures, microsecond serialization, mismatched maps and
tampered notes, adjustment records and duration claims. This policy extends the
earlier strict boundary behavior documented in `songsterr-audio-boundary.md` and
`songsterr-silent-terminal.md`; it does not establish that the source author's
timing map is musically correct.
