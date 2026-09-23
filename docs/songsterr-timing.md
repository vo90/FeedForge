# Songsterr authored timing

The importer preserves written rhythm separately from performed attacks. It
does not repair notation or reproduce synthesizer humanization.

Semantics checked against Songsterr's public assets on 2026-09-23:

- https://www.songsterr.com/howtoreadtab
- https://static3.songsterr.com/production-main/static3/latest/common-DWZ1FVtGLtYGNv20.js
- https://static3.songsterr.com/production-main/static3/latest/FluidsynthAudioPlayerWorkerEntry-BZJ7IhMqUXKWqzD-.js

An on-beat grace group takes time from the principal beat, without extending
the measure. Its total duration is limited by the average encoded grace
duration and the principal's available time. The principal reserves one half,
one quarter or one eighth for undotted, singly dotted or multiply dotted grace
groups. An over-budget group divides its budget equally. A final principal's
available time is the remaining measure. Orphans, before-beat groups and groups
with no positive principal duration are reported, never repaired.

Brush strokes and arpeggios supply direction, duration (0–960) and shift
(0–100). Duration is capped at half the written beat and at 960 units. The
attack spacing in quarters is capped duration / (480 × note count). Shift 100
starts on the beat; lower shifts move the group earlier. Strings are ordered
high to low for up strokes and low to high for down strokes. Offsets remain
exact fractions: MIDI tick flooring, synthetic note-off shortening by one
tick, automatic strumming and humanization are not authored tab information.
Linked bends/slides/hammer-ons disable strum spreading in the published player.
Conflicting directions, tied strums and grace/strum combinations remain explicit
unsupported cases. Authored simultaneous chords stay chords; staggered attacks
are separate playable notes with their original chord notation preserved.

Legacy `upStroke: 1` means a downward brush, and `downStroke: 1` an upward
brush, with duration 30 and shift 100. Other legacy values are not guessed.
The independent `pickStroke` field describes ordinary picking direction.

Staccato shortens the performed duration to half, with Songsterr's 1/128 whole
note minimum. Written duration is unchanged. Cases where that floor would
extend the note, or a linked technique depends on its former endpoint, require
review. The staccato annotation remains in the retained source and report.

These are FeedForge conversion rules, not a claim about support in a particular
installed game version. Original bytes and unsupported fields remain available
in the FeedPak and durable import evidence.
