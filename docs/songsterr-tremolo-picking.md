# Songsterr tremolo picking

Preservation contract 43 supports `note.tremolo` as well as `beat.tremolo`.
A beat instruction applies to all its notes; a note instruction applies only
to that string. FeedBack's existing `tr` display is used, without expanding
separately scored repeated picks. Original rational subdivisions are preserved
in the embedded source and a located compatibility finding states the display
and rate limitation. This is picking, not a whammy-bar curve.

Positive integer rational pairs and the established boolean instruction are
accepted. Absent/null/false remain inactive, matching the importer's existing
flag policy. Other values fail with a source location. Both fields are validated
even when the beat instruction overrides the per-note rate.

The pinned worker `4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab`
was probed through its complete scheduler with the existing authored profile.
Its `js` stage uses the beat rate first, then the note rate; note `true` uses
1/32. A two-string quarter-note probe generated three extra note-on events on
the marked string at 1/16, seven at 1/32, and one per string for beat 1/8.
With both beat 1/8 and note 1/16, both strings used 1/8. The unmarked string
had no additional events when only one note carried tremolo.

The small synthetic results are in `tests/fixtures/songsterr_tremolo_reference.json`.
Regenerate into a new file with
`node tools/songsterr_compatibility/tremolo-reference.cjs PINNED_WORKER NEW_OUTPUT`
and review differences; ordinary tests do not download or execute player code.

This qualifies field scope and rate precedence, not complete synthesized/game
equivalence. The worker also treats explicit note `false` as a present boolean
and synthesizes repetitions; we do not copy that inconsistent behavior into
an inactive flag. Its automatic chord attack staggering is also not imported.
Source synthesis rates are not new gameplay rules.

Ties retain the established beat-level mapping: a merged sustain has one `tr`
instruction if an authored segment carries tremolo. Its onset/cessation within
the tie cannot be displayed separately, and this is disclosed in the located
compatibility finding. The written boundaries remain in the embedded source;
no attack or duration is changed. A proposed stricter guard exposed this
existing limitation in Scar Tissue and was removed before integration.

The converter and verifier independently validate/map the fields. Regression
tests cover chord membership, repeats, ties, malformed values, independent
archive mutations, retained rate reporting and Hybrid Lead donor preservation.
No old song packages are patched, and no game code is changed by this mapping.
