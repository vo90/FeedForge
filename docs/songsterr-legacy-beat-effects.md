# Legacy Songsterr beat effects

Preservation contract **91** accepts two narrowly defined legacy beat fields:
`harmonic` and `fadeIn`. Every present value, including `false` and `null`, is
counted in the source inventory and compatibility report. Original source bytes
remain in the FeedPak. Neither field is copied into a chart note or used to
invent a playback effect.

## Harmonic summary

An active boolean `beat.harmonic` is accepted on a selected guitar/bass beat
only when its nonempty sounding note set already consists entirely of valid,
explicit natural harmonics. The existing note-level conversion determines
touch position, pitch, capo, attack, timing and sustain. The beat flag adds no
harmonic and changes no target. Empty/rest beats, missing or mixed harmonic
instructions, conflicting harmonic data, dead notes and scrapes do not qualify.

The pinned source player produces identical complete schedules when these
redundant flags are removed. Explicit note harmonics remain positive controls:
removing the actual note instruction changes pitch. A boolean beat summary must
never become a note-level harmonic enum or be spread across ordinary notes.

## Fade-in disclosure

An active boolean `beat.fadeIn` in a selected arrangement is retained as a
display/expression limitation: the authored volume-swell instruction remains in
the source, while the game has no dedicated swell display or scoring envelope.
Its notes, bends, attack, duration and notation retain their existing meaning.
No audio automation or guessed swell timing is generated. Inactive flags are
retained source metadata.

## Boundaries and independent checks

Only literal JSON booleans or null are accepted. Integers, strings, arrays and
objects are invalid even on rests or excluded tracks. Excluded tracks receive
source-only accounting; their unrelated note vocabulary is not qualified as
guitar/bass music and an excluded fade-in is not reported as a playable effect.

The independent reader implements these checks without importing the producer
helper. The archive verifier requires complete, correctly typed source-bound
findings, their retention status, and contract/report version 91 when the new
fields are present. It also checks the actual chart and notation. Missing,
duplicated or altered findings, invented effects and changed harmonic targets
fail verification. Earlier contract 90 plain-tie packages remain verifiable
when they do not contain these new fields.

This change does not accept the separate negative-fret mute alias or differing
stored fret on an expressive bent-origin tie. Those guards remain in place.
