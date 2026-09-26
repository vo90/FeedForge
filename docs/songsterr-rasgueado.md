# Rasgueado instruction (preservation contract 29)

The boolean Songsterr beat field `hasRasgueado` is an optional playing instruction.
The approved policy retains it in the original source and a located nonblocking
compatibility finding. The game plays and scores the authored notes normally:
no rasgueado label, new gesture, repeated attacks, or scoring requirement is added.
Written notes, ties, durations, and independently specified brush/arpeggio timing
are unchanged. This is an expression limitation, not a note omission.

Only boolean values are admitted (null/absent is inactive). False is inactive;
malformed values remain source errors, including zero, strings and containers.
The separate pattern-valued `rasgueado` field remains decision D5: it can carry
rhythmic information that cannot be inferred from the boolean instruction.

The independent source reader validates the flag separately. Package verification
checks the retained source bytes, exact finding coverage/location/value, and all
ordinary note/chord/timing comparisons. A reported limitation never excuses a
missing note, duplicate attack, changed pitch, or changed sustain.

Contract 29 invalidates cached conversion identities while preserving access to
historical reports. No Core, Desktop, TabView or NoteDetect update is needed.
