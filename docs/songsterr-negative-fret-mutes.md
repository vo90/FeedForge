# Legacy negative-fret mute interpretation

Preservation contract 92 admits a sounding guitar or bass note whose authored
`fret` is exactly the JSON integer `-1` and whose `dead` flag is literally `true`.
It converts to the existing `f: 127, mt: true` unpitched mute instruction. This is
the policy `songsterr-negative-fret-mute-v1`; 127 identifies neither a physical
fret nor a MIDI pitch. Existing missing/null-fret mutes and positioned dead notes
at fret zero or a positive fret keep their previous interpretation.

The original source bytes retain `-1`. The parsed note also stores that authored
value separately from its effective game target. A muted-tie identity receipt
retains an authored `-1` when the existing tie rule needs a receipt; it continues
to report the origin's effective target separately. The source and archive
declare `negativeFretMutePolicy` only when a selected playable note uses this
alias. The compatibility finding `note.negative_fret_mute`, located at the raw
note's `/fret`, has category `source_interpretation`, impact and work status
`source_retained`, and retention `original_source`.

The game's existing muted-note handling displays an X and excludes muted notes
from pitched detection targets; mixed chords still keep their pitched members.
An arrangement containing an unpitched mute omits optional standard notation
and discloses `notation.unpitched_mute`. No fabricated notation MIDI pitch is
introduced. Source string, attack, held endpoint, chord membership, repeats,
ghost and accent markings remain independently checked.

The native reference establishes source scheduling, not an audio repair. Its
`fc` preparation preserves numeric `-1`, while missing/null frets become zero;
`uc` calculates nominal pitch from tuning, capo and that value. Consequently a
null control has a nominal mute pitch one semitone higher. `Mo` then selects
mute synthesis behavior and may shorten the emitted envelope. Later chord
preparation can change final attack order with nominal pitch. The game alias
does not reproduce this sample pitch or guarantee identical synthesized audio.

The development fixture contains 49 bounded cases and 204 exact comparisons of
the complete pinned worker, extracted runner and existing reference runner
across `authored`, `legacy-brush-authored-v1` and `player-defaults`. Its 21 guard
cases document native observations without granting converter support. Null
controls compare authored and held clocks exactly; a mixed-chord final-clock
difference is retained explicitly. The worker SHA-256 is
`4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab`.
The reviewed worker remains outside Git; the manifest and worker bytes are not
changed. Reproduce the compact fixture with
`tools/songsterr_compatibility/negative-fret-mute-reference.cjs` and run
`tests/test_songsterr_negative_fret_mute_native.py`.

Other negative frets, integral floats, strings, coerced values and nonliteral
dead flags do not qualify. Existing pitch-gesture, link, tie-continuity and
string guards still apply. Inherited active beat vibrato or tremolo-bar
expression remains blocked for this new alias, including shapes that the old
null-fret parser could read. This extension does not expand that older boundary.

The cached StoreWorker's general negative-fret validator reports a fret-range
error and proposes zero without a dead-note exemption. Native synthesis
acceptance therefore does not establish that `-1` is valid official editor
schema. This policy is a conservative game interpretation of an explicit dead
instruction, with authored provenance retained.

The retained Brothers In Arms (Acoustic), song 59690 revision 74977, has 38
literal `-1/dead:true` notes without note-level pitch effects or ties. Its source
SHA-256 is `0ee7846a6857f08a6238760abc6f437b6d820396cc62d29cbb160fbb3ed13996`.
An in-memory null contrast of those 38 notes preserved source and final clocks
across all three profiles but changed 76 emitted note-on/off pitches per profile.
That observation does not establish whole-chart import support: its separate
bent-tie contexts remain outside this qualification.
