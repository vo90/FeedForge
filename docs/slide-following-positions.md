# Moving hand positions for known slides

## Plan and scope

1. Replace the generated full-slide corridor with timed occupied fret cells.
2. Cover overlapping notes, both fret-spacing settings, and slides to fret zero.
3. Version the generated guidance and upgrade verified older imports in the
   game's memory using the byte-identical shared policy.
4. Verify independent continuous geometry, archive integrity, and the production
   Stable Straight camera. Keep the camera controller unchanged.

## Behavior

`slide-follow-v1` generates hand-position anchors along known `sl` and `slu`
destinations. It uses the highway's pitched/unpitched easing and the union of
occupied cells in its uniform and logarithmic fret layouts. Checkpoints occur
at crossed fret wires and are bounded by the 24-fret neck, not playback FPS or
note duration. Four-fret regions move only as needed; other sounding strings
can require a wider region.

Cirice's Hybrid Lead note at 267.38s is encoded as fret 13 to fret 0 over 5.34s.
Its lane now descends from 12–15 to 1–4 and stays there through the following
unpitched note and rest. Fret zero is a known endpoint at the nut; it does not
create a fictitious zero-based hand position.

Known slides retain their original duration/easing when a later attack ends
their guidance on the same string. Targetless slide-in/out cues do not invent
motion. Natural-harmonic contact positions retain conservative coverage.
Open-string preparation, explicit compact legato, and chord preferences still
apply. No notes, sustain lengths, scoring instructions or source files change.

## Compatibility and validation

New guidance records `positionPolicy: slide-follow-v1` and
`slidePolicy: timed-known-slides`. The verifier still understands older
`known-corridor` receipts. Its new check evaluates each active slide's continuous
geometry at the endpoints of every anchor/attack/release interval instead of
replaying the generator. Rehashed but stale anchors fail coverage verification.

FeedBack contains the same `generated_hand_positions.py` under `lib/`. Its
existing hash-guarded loader upgrades recognized previous policies, now including
`open-preparation-v2`, in memory. Authored or edited guidance, unknown policies,
and current-policy archives remain intact. Difficulty windows use the same path.
The existing `tools/audit_generated_positions.py` checks source parity and all
selected archives without rewriting the library.

## Verification

The targeted suite covers short/long pitched and unpitched slides, both fret
layouts, descending to zero, slide precedence, overlapping holds, replacement
picks, old policy regeneration, immutability and independent tamper detection.

Validation on 2026-10-04: 165 focused FeedForge tests, 21 Core loader tests,
112 camera/region tests, and six rendered Stable Straight cases passed. A
read-only audit covered 64 arrangements in 14 songs, with music and archive
hashes unchanged. The broad FeedForge run passed 4,925 tests with five skips;
its one CLI subprocess import-path failure passed when rerun with this
checkout's `src` in `PYTHONPATH`. The final focused run includes two additional
fractional/harmonic contact cases added after the broad run was collected.

The Core browser harness accepts `--case slide-follow --slide-fixture <json>`.
The JSON contains `bundle`, `start`, `end`, `capture`, `startFret`, and `endFret`.
It renders the production Stable Straight camera in both fret layouts at 0.5×,
1× and 2×; checks moving lanes, retained camera targets, geometry visibility,
and direct-seek consistency; and saves screenshots. Runtime integration uses
the ordinary reviewed Songsterr merge and paired-runtime lifecycle.
