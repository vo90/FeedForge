# Songsterr picking-hand fingering

Preservation contract 44 recognizes `note.rightFingering`: `P`, `I`, `M`,
`A`, `C`. These are picking-hand teaching annotations, distinct from
`leftFingering` and FeedBack's fret-hand `fg` number. A valid annotation does
not block conversion. It remains in the embedded original source, field
inventory and a located compatibility finding. Picking-hand letters are not
currently engraved or scored. All attacks, pitches, durations, techniques and
existing fretting-hand hints remain unchanged.

Absent/null means no annotation. Unknown values and wrong types, including
false or empty strings, fail with a source location; no thumb/finger is guessed.
Converter validation and independent verification are separate. The package
checker derives the expected report entries from the source, so losing or
changing an annotation cannot be hidden by reporting a successful import.

The reviewed common schema SHA-256 is
`8b9267cd39f7f3b0511bade44de01cf3fe7c8025d5a7d534f8a6de448c940e17`.
It explicitly declares the five values separately from left-hand fingering.
The pinned worker
`4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab`
does not read this field. Complete scheduler probes with chords, ties and
repeats produced identical timing/pitch/event traces with each annotation.
This establishes annotation handling for those examples, not every engraving
or instrument-specific synthesis behavior.

Regenerate the reviewed fixture into a new file with
`node tools/songsterr_compatibility/right-fingering-reference.cjs WORKER COMMON_SCHEMA NEW_OUTPUT`.
Ordinary tests use only small synthetic fixtures; the released app never
downloads or executes the reference. No existing song packages are rewritten
and no game repository change is needed.
