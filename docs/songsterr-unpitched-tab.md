# Unpitched Songsterr guitar and bass strikes

An explicit `dead: true` note with no authored fret converts to FeedBack's
existing `f: 127, mt: true` wire representation. 127 is a sentinel, not a
physical fret or MIDI pitch. Authored frets, strings, timing, ties, ghost and
accent markings remain unchanged. Missing frets on pitched notes remain errors.

An arrangement containing these strikes omits optional standard notation;
`notation.unpitched_mute` records the display limitation. The original source
bytes remain embedded and archived. Other arrangements can retain notation.
The playable tab, complete arrangement coverage and audio-alignment checks remain
required. No artificial MIDI pitch is generated for a notation consumer.

Sentinel chord-template slots require an explicit unpitched muted child on every
use, including generated difficulty levels. Template-only, unflagged and
unreferenced sentinel slots remain invalid. Pitch gestures on unpitched notes,
and pitched links ending at them, remain unsupported rather than gaining an
invented fret. Ordinary fretted mutes still keep their authored fret.

Preservation contract 9 verifies this representation independently and rejects
changed/missing notes, strings, mutes, durations or fabricated notation. Older
conversion reports remain readable but do not receive current verification or
reuse status. Existing game mute rendering and scoring rules are unchanged.

This does not implement richer harmonics, whammy techniques, pick scrapes,
trills, rasgueado, extended physical frets or simultaneous same-string voices.
