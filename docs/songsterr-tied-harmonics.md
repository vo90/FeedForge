# Tied harmonic interpretation

The first picked note owns the attack and held fret. Repeated or absent harmonic
fields inherit the established target. A changed harmonic on a tie does not
create another picked note. Ambiguous changes retain that target, preserve the
source annotation, and produce a located, nonblocking representation limitation.

One clear ordinary-to-artificial-harmonic continuation can become a delayed
contact. An intervening conflicting harmonic/feedback instruction, or a slide
whose contact position cannot be resolved, uses the limitation fallback.
These are general source-data rules; they contain no song-specific exceptions.

The additive `harmonic_changes` v1 object has exactly one event:

```json
{"version":1,"events":[{"start":0.15,"end":0.3,
  "target":{"kind":"artificial","node":7,"interval":19,"policy":"harmonic"},
  "source_id":"songsterr:3:38:0:5:0"}]}
```

Start/end are seconds relative to the ordinary attack. Start must be positive
and strictly before end; end must equal the note's sustain. Nonlinear audio
alignment maps both absolute endpoints independently. Only a held tail after
the contact can be shortened by the existing verified audio-end policy.
The initial note must not also carry a harmonic or fret-hand-mute target.
The contact position is held fret plus node; the sounding partial is distinct
from both positions. Separate authored bend/whammy data remains separate.

`import/tied-harmonics.json` v2 retains written location, performed occurrence,
score-time bounds, authored/used targets and the applied rule. It is bound to
the unchanged archived source by SHA-256. Independent verification reconstructs
both contact data and fallback evidence from the source; it does not call the
production classifier. Preservation/cache contract 22 supersedes 21.

The matching game displays a narrow contact band and `AH <position>` on the
existing sustain. It keeps one scored attack, ends that attack's late matching
window at the contact, and resolves subsequent held-note feedback from song
time. This requires the updated core, NoteDetect and Desktop contact-v1 support.
Older games can ignore the additive field, but cannot represent this cue
faithfully. TabView's GP bridge retains tied contacts with a quantized display
rhythm and identifies limitations; the exact source and game timing are retained.

This does not infer gestures for arbitrary pinch-to-pinch changes, delayed taps,
feedback mixtures or changing natural harmonics. Source musical correctness and
physical instrument acceptance remain distinct from software fidelity checks.
