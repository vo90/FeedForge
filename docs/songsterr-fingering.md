# Authored Songsterr fingering

Preservation contract 41 converts optional `note.leftFingering` teaching marks
to FeedBack's existing `fg` field. No pitch, onset, sustain, scoring target or
inferred fingering is added or changed.

| Songsterr value | FeedBack note | Meaning |
| --- | --- | --- |
| `"1"`–`"4"` | `fg: 1`–`fg: 4` | Index, middle, ring, little finger |
| `"T"` | `fg: 0` | Thumb |
| `"0"`, null, absent | No `fg` | Open/no finger, or unspecified |

Songsterr's public note schema uses these string values. Numeric values,
booleans and unknown labels are rejected with a source location. In particular,
Songsterr zero must not be cast to FeedBack zero, which means thumb.

Chord templates carry the same hints on the matching strings, with `-1` for
unspecified strings. Fingering is part of template identity: identical frets
with different fingerings must not share a template. Voice projection,
unsupported-fret omission and Hybrid Lead retain these fields.

A tie continuation cannot change the finger displayed at the original attack.
Continuation finger instructions stay in the complete original source and are
listed as a display limitation. Likewise, an open/no-finger instruction on a
fretted note is retained and reported without changing the fret. Generated trill
attacks receive no guessed finger numbers; only the authored onset has its hint.

FeedBack already reads `fg` for optional finger hints on the 2D/3D highways and
chord-template `fingers` for fingering help. This converter change requires no
new game technique or scoring rule. Existing display settings still control
the hints. Source fingerings are not guaranteed to be pedagogically correct.

Independent source-to-package verification checks missing, changed and invented
hints, value types, chord-template consistency and source-only limitations.
Tests also cover strums, repeats, ties, trills, voice projection, omitted high
frets and Hybrid Lead donor passages.
