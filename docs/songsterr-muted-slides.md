# Muted slides (preservation contract 28)

Songsterr's dead notes without a fret remain `f:127, mt:true`. The sentinel is
never a physical fret or a pitch target. Explicit upward/downward slide-outs
retain timed `slide_out_marks`, ghost articulation, string, and attack/tie timing.
They are unscored directional visual gestures, not pick scrapes.

A shift starting from X keeps the original muted attack and tie. Its next
explicit pitched attack stays playable. The exporter does not write `sl` or
invent a starting fret for X. `import/muted-slides.json` retains the shift and
destination with source identity, occurrence, score-time bounds and source hash.
The independent reader reconstructs this evidence and verifies the resulting
chart and reported display limitation. Ambiguous legato, pitched-to-X links,
unresolved targets, repeat-jump links and other unsupported pitch gestures still
fail rather than receiving fabricated targets.

The same evidence also records directional muted slides. Contract 28 invalidates
older conversion caches and is required to publish these interpretations.
Original source bytes are retained unchanged. Existing notation limitations for
unpitched standard staff notation remain; guitar/bass tab is displayed as X.

Updated game rendering is required for muted directional trails. The source
interval is preserved; visual travel distance and speed are presentation, not
authored fret or pitch data. A muted shift with an unspecified starting fret has
no invented individual fret path. Neighboring pitched slides render normally.
