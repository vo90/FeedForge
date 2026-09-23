# Arrangement diagnostics and deferred work

Every guitar/bass part is assessed with its original index and ID. Diagnostic
selection does not mutate the source, remove other parts' global meter/tempo/
repeat data, or authorize partial publication. Score-ready means parsing and
timeline generation passed; audio and independent package verification follow.

Compatibility inventory 6 records technical work, display limitations and
design decisions. Decision IDs correspond to the approved deferred register:
D1 pick scrapes; D2 whammy/tremolo bar; D3 richer harmonic data; D4 trills;
D5 rasgueado; D6 incomplete unpitched-mute consumer support. No substitute
playing instructions or new scoring behavior are introduced.

The durable list retains examples, values, song/revision, arrangement and
source hash. Historical gaps move to resolved only when a later assessment
has an independently verified current-contract package for the same revision.
A finding disappearing from preflight alone is insufficient. Alignment
failures retain both source-map and matcher diagnostics in the evidence.

The existing unpitched mute sentinel cannot yet be enabled generally.
At the inspected FeedBack dev/integration baseline, 3D recognizes fret 127
with mute, but the 2D draw path feeds 127 directly into fret positioning and
its ordinary note label. The converter's notation also derives MIDI from the
physical fret. A complete, unpitched round trip is therefore not established.
This phase makes no game, notation-contract or scoring changes.
