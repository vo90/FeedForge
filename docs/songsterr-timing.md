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
available time is the remaining measure. Before-beat grace groups within a
measure borrow from the preceding principal and end at the following attack,
or at that preceding principal's endpoint for a trailing group. Written grace
uses the existing acciaccatura value `a`; on-beat uses `p`. Boundary-crossing,
recording-start and shared-principal combinations stay unsupported rather
than invoking the public player's fallback that changes grace type or clips
notes. No measure length is added.

Swing follows the public `Mi` / `ji` / `Ai` transformation: carried eighth
or sixteenth feel, 2:1 default, 3:1 dotted and 1:3 Scottish. Tuplets with
nonintegral subdivisions (and duplets under default feel) exempt their pair
across the part's voices. Incomplete pairs remain straight. Performed lengths
change; written durations and beat positions do not.

Enabled gradual tempo follows `Ur`: a destination's linear flag interpolates
from its previous event, with at most 64 steps and rounded intermediate BPM.
Disabled gradual tempo leaves ordinary tempo steps. Positions use static
960-tick quarter coordinates (see `songsterr-tempo-coordinates.md`). Combined
ramps and explicit holds use the order in `songsterr-combined-tempo.md`.

Explicit fermata automation follows `Qr` / `$r` / `ei`: positions are
960 ticks per quarter, the held span is the measure's denominator beat or its
encoded binary subdivision, and the temporary tempo is
round(BPM × (4/5 − 7×length/15)). The previous rate resumes afterward unless
an explicit event already occupies that boundary. Only encoded lengths 0–1,
supported tempo units (see `songsterr-fermata-units.md`), nonoverlapping holds
and measures without midbar tempo changes are accepted. Free-time or
unspecified holds are not guessed.

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

Legacy arpeggios without modern strum data follow the public audio worker's
`ws` / `ao` path: `upArpeggio` spreads from the high string and
`downArpeggio` from the low string. An integer value v (1–8) gives a spacing
of 4 / (13 × 2^(8−v)) quarters. This is different from the editor validator's
optional migration to duration 240; applying that migration to a captured old
tab would change its existing playback. Explicit modern arpeggio/brush data
takes precedence, matching the performer. Conflicting legacy markings stay
unsupported. Tick quantization and synthesizer note-off adjustments are not
copied into the authored rational clock.

The single undotted whole-rest glyph (`type: 1`, duration 1/1, rest-only
notes) occupies the complete current measure, including irregular meters.
Ordinary overflowing notes/rest sequences are not shortened. Written whole-rest
notation and the original source remain unchanged.

Bends with `precisePosition` on every point use those percent coordinates
instead of the old 0–60 coordinates. Source ordering is validated; equal
positions take the last authored value, and the last point is held through
the remaining sustain. Mixed precision, out-of-range or reversed coordinates
are rejected. These remain finger bends, separate from tremolo-bar gestures.

Beat-level `slapping` and `popping` map to the existing slap/pop chart flags
and notation annotations. Explicit supported clefs are carried into measure
staff overrides without transposing physical notes. Dotted tempo units multiply
their ordinary quarter-note rate by 3/2.

These mappings use preservation/verifier contract 6 and compatibility inventory
5. Older evidence remains readable but cannot satisfy a current verified import.

Staccato shortens the performed duration to half, with Songsterr's 1/128 whole
note minimum. Written duration is unchanged. Cases where that floor would
extend the note, or a linked technique depends on its former endpoint, require
review. The staccato annotation remains in the retained source and report.

These are FeedForge conversion rules, not a claim about support in a particular
installed game version. Original bytes and unsupported fields remain available
in the FeedPak and durable import evidence.
# Brush display grouping

Fresh Songsterr imports retain the distinction between a brush and an arpeggio
in `import/strums.json`. Complete, staggered brush groups also receive the
existing display-only note field `ch`. The group ID is the index in the
package-wide source evidence, so Hybrid Lead can copy multiple donors without
colliding IDs. The manifest records `strumGroupingPolicy: authored-brush-groups-v1`.

Each member stays in its original note array at its actual attack time, with
its original duration and techniques. A group does not create a simultaneous
attack or change scoring. The highway uses it for one enclosure at the first
attack; following members retain their spacing and do not acquire separate
stems. Arpeggios, unrelated nearby notes, incomplete groups and ambiguous
members do not acquire this grouping. Simultaneous chords keep their existing
chord representation. Independent archive verification checks the display IDs
against the raw source as well as verifying the musical events.
