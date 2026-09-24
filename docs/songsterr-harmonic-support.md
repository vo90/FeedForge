# Harmonic support: preservation contract 16

This additive local extension preserves fretted harmonics separately from the
natural-only `hn`/`hps` pair. A playable note can contain:

```
harmonic_target: {kind: "tapped", node: 7, interval: 19, policy: "harmonic"}
```

`f` remains the held fret. `node` is relative to that fret, and `interval` is the
source's equal-tempered approximation of the harmonic above that fretted pitch.
The actual partial ratios determine scoring frequency. AH/TH contact information
is `f + node`; it is not a second playable note. This example at f=12 means hold
12, tap at 19, and sound the third partial of the fretted string.

Policies are validated against kind: pinch/artificial/tapped use `harmonic`, semi
uses `mixed`, and feedback uses `attack_either`. Semi retains its original type
while displaying as pinch. Feedback keeps the initial note/sustain; either the
ordinary fretted or harmonic pitch can satisfy its one initial target, with no
feedback-specific sustain-pitch requirement. There is no automatic score award.

Pinch/semi retain `hp: true`; AH/TH and feedback do not masquerade as natural
harmonics or ordinary taps. Old hp-only charts keep their old behavior. No pitch
is invented when that old representation provides none.

The verified Songsterr natural/fret-15/node-15 case maps to `hn:14.7,hps:34` and
`harmonic_alias:"songsterr-natural-15"`, with f=15 and hm=true. It agrees with the
captured source playback's seventh partial. No general nearest-node rounding is
allowed. Raw source remains unchanged and the compatibility report records the
interpretation.

These are general source-notation rules, independent of song, artist or revision.
Explicit precise positions override the displayed integer shorthand (for example
3/3.2 and 15/14.7). The verified 15/15 alias applies wherever that same source
signature occurs. Further aliases require a verified intended partial; guessing
the nearest harmonic from an unfamiliar number can change the music.

The original source identity/type/value, duration, ties, bends, slides and ghost
marks remain preserved. Ties cannot silently change the target or add attacks.
Whammy effects and other independently unsupported forms are not excused by this
extension. Deploy with matching Core, Note Detection, Desktop and Tab View changes;
source conversion alone does not establish consumer or real-instrument support.
