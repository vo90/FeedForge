# Repeated broken power-chord positions

## Contract and implementation plan

Independent chords still establish their box at their lowest fretted member.
Do not replace this rule with general reuse of a containing four-fret lane.
Thunderstruck's repeated A2 / D4+G4 attacks may instead share the complete
2,4,4 power-chord position. The earlier Rats slide landing and wide-position
release behavior remain acceptance requirements.

1. Recognize repeated split power chords during position generation only.
   Require a picked root and an upper fifth/octave pair on three adjacent
   strings, consistent contacts, correct pitches under the stored tuning,
   and a combined span of at most four cells. Confirm at least two complete
   root/upper-pair cycles before assigning any shared position.
2. Bound matching by adjacent attacks and local beat duration. Conflicting
   contacts, gestures, intervening events, and long gaps terminate a group.
   Upper contacts may repeat or be picked separately while the root keeps
   returning. The terminal cycle includes only its first complete fifth/octave
   response; subsequent independent dyads establish their own lowest fret.
   Each candidate examines at most sixteen root picks and sixteen responses.
   Adjacent attacks must be within one local beat (0.5 s without beats,
   bounded to 0.1–1 s); a complete response must arrive within two beats.
3. Use the complete shape's lowest fret as a preference at confirmed attacks.
   Existing occupancy, slide tracking, sustain release, neck bounds and
   positionless preparation remain authoritative. Never fabricate a played
   root, sustain, chord, fingering, arpeggio or handshape.
4. Version the policy as `broken-power-chord-v1`; synchronize the pure Core
   copy and the known-policy checks. Preserve versioned ownership on load.
   Explicit regeneration retains source/user/edited/unknown guidance guards.
5. Validate independent-chord/Rats tests unchanged, recognition and rejection
   cases, actual Thunderstruck data, package integrity, rendered lane/box
   agreement, and generation performance. Work in isolated checkouts and use
   disposable regenerated packages; keep original songs and profiles intact.

Matching is prepared before playback, so the first confirmed cycle and seeking
use the same positions. No detection or chart scanning is added to rendering.
This deliberately does not attempt general chord or fingering inference.
At least one real simultaneous dyad must occur within the confirmed passage;
an unrelated chord elsewhere cannot qualify a sequence of single notes.
Explicit chord/template arpeggios and templates containing additional contacts
retain ordinary behavior. The matcher reads musical inputs and beats only;
existing handshape output never becomes an untracked generation dependency.

## Scope and ambiguity

An identical upper dyad between recurring roots is part of the same inferred
grip; tab alone cannot distinguish an intended independent same-pitch reply
without a changed contact, timing boundary or authored position. Existing
supplied positions remain authoritative. Inference does not add fingering
claims or alter source chord membership.

Six-string tuning offsets do not distinguish guitar from six-string bass.
Recognition therefore uses only the two lowest three-string groups, whose
intervals agree for both instruments. Higher groups retain chord-local
positions until the format supplies an explicit, hashed reference tuning.
This restriction does not affect Thunderstruck's A/D/G grip.

## Validation

The focused import, generation, provenance and Hybrid Lead suite passes 411 cases,
including unchanged independent-chord and Rats slide regressions. Core loader
and serialization coverage passes 306 cases; 137 existing highway tests pass.
The new pattern tests cover repeated and separately picked upper contacts,
terminal independent chords, timing limits, conflicting sustains, techniques,
arpeggio markers, ambiguous tuning, ownership and handshape invariance.

The actual Thunderstruck Hybrid Lead riff at 110.220–113.596 s remains at 2–5.
Eight production-renderer checks pass across both cameras, seeking and
continuous playback. The 14-chart audit preserves every Rats/Cirice generated
result and all seven supplied nonempty anchor arrays. A disposable regenerated
Thunderstruck package validates with its Hybrid Lead hashes refreshed; music,
audio, source assets and the original archive remain unchanged.

Fresh-process paired timings measured approximately 30 ms before and 39 ms
after on Thunderstruck Hybrid Lead: about 9 ms added once during generation.
A 50,000-attack stress chart took approximately 2.84 s before and 3.40 s after.
No recognition runs per frame. Evidence and private-song previews are stored
outside the repository in the workspace's dated acceptance artifact directory.

The entire Forge suite was not completed: a broader run encountered the
cent-offset conversion comparison failure, also reproduced on the unchanged
Songsterr branch. That existing test compares guidance hashes after changing
the pitch input. The focused suite above is the validation claim for this
change; the broader suite is not claimed green.
