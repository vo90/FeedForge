# Explicitly silent voices

A voice with `rest: true` and no `beats` member represents the same silent voice
as `rest: true, beats: []`. Interpret it through a read-only beat accessor; retain
the original source bytes and voice index. Never synthesize beats or remove the
voice. The existing measure, pickup, swing, repeat, lyric, tone and rest-tie
rules apply unchanged.

Only an absent member receives this interpretation. A supplied malformed list,
missing/non-boolean rest flag, or unknown active voice field is still rejected.
The independent source reader implements its own structural check, including
whole-voice rest evidence. Source and archive tampering checks remain active.

This is additional accepted source syntax, not a new musical transformation or
player limitation. Preservation contract 125 remains unchanged: existing valid
archives keep their semantics and the previously rejected compact form has no
published output to reinterpret.

Qualification: the retained public worker with SHA256
`4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab`
schedules compact and explicit-empty forms identically, under authored and
player-default profiles. Both extracted and complete worker comparisons agree.
The motivating score contains nine compact secondary rests; this rule has no
song, revision, instrument or bar-number exceptions.

Implementation and validation sequence:

1. Add producer accessors to timing, parsing, pickup, lyrics and tone consumers.
2. Independently accept the same syntax in source and archive verification.
3. Test primary/secondary silence, pickups/swing, repeats/ties, lyric slots,
   sound changes, rejection boundaries, exact retained source and tampering.
4. Replay unchanged source, build the reviewed integration revision, refresh the
   single shared runtime through its guarded workflow and run a fresh app import.
5. Reconcile the paused batch only after fresh package and game-loader acceptance.

Regression coverage: `tests/test_songsterr_rest_only_voices.py`.
