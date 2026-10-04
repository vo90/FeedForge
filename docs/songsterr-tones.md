# Songsterr tone timelines

Preservation contract 73 exports the source track's initial sound and explicit
sound changes for native Songsterr JSON imports. It writes the existing FeedPak
`tones.base` and `tones.changes[{t,name}]` fields. These are sound identities and
switch times; no amplifier settings, rigs, presets or `rigs.json` are invented.
GPIF and PSARC conversion retain their existing behavior.

The source is `instrumentId`, `sounds[{instrumentId,label}]` and
`trackAutomations.trackSoundAutomations[{measure,position,soundId}]`. A missing
part program uses the explicit metadata program; when neither exists, the base
is explicitly **Unspecified**, not a guessed clean/distorted sound. A missing
change list means a constant initial sound. Invalid references, unsupported
active automation fields and malformed sound definitions block conversion.

Names contain the readable source label and a deterministic identity scoped to
the song, original track and sound entry. Duplicate labels cannot collide. The
initial program reuses a catalogue entry only when exactly one entry matches.
Different catalogue entries are retained even if their GM programs match.
Voice-split arrangements inherit the original sound identities. A sound program
does not change the arrangement's guitar/bass classification, and section labels
do not create inferred solo boosts or clean/distortion transitions.

## Source clock

The implementation follows the public Songsterr player pinned by
`tools/songsterr_compatibility/reference-manifest.json`, SHA-256
`4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab`.
`tests/fixtures/songsterr_tone_reference.json` records native model schedules in
both the authored and player-defaults profiles. No downloaded worker is bundled
with the converter or required at import time.

Static sound positions use 960 units per nominal quarter, scaled to the actual
bar length before tick quantization. This matters in shortened pickup bars.
The adaptive synth tick resolution is calculated across the source parts.
The player's tie guard examines cumulative **duration fields**, not swung beat
start times or strummed note attacks. Its one-tick delay applies only when all
non-rest notes at that boundary are tied.

Changes are expanded through performed repeats and alternate endings and use
the same score-to-seconds tempo clock as notes. Repeats do not introduce implicit
sound resets. At a coincident timestamp the last source instruction wins; source
order is retained in the receipt. Terminal events with no remaining performed
score interval do not reappear as transitions in appended audio silence.

## Recording clock

The builder consumes the finalized alignment, after preparation has incorporated
the sample-counted opening silence. It maps tone times exactly once through the
same affine or piecewise-linear function as notes. Tone events never determine
how much opening padding is needed.

Events at or before recording time zero establish the base state. Future events
are not promoted to the base merely because they precede the first note. Events
at or beyond the retained recording end are omitted. Changes during rests and
held notes remain. Consecutive identical states are compacted; distinct source
times requiring different sounds cannot silently collapse at six-decimal export
precision. Historical contracts remain verifiable without tone metadata; a new
contract-73 native import must have it.

Arbitrary interior audio deletion is not an existing importer operation. A future
editor must expose retained source spans to all timeline consumers and inject
the state active at every resumed source position. Changing audio independently
after export cannot preserve synchronization. Actual audible rig-switch latency
and the game's millisecond/polling resolution are outside this importer policy.

## Hybrid arrangements

Hybrid sound ownership follows each validated passage's owned region and complete
selected gestures. Entry uses the donor's current sound, internal changes follow
the donor, and return uses the main track's current sound at that later time.
The schedule is rebuilt instead of inheriting the deep-copied main chart.

Overlapping owners or retained simultaneous gestures requiring incompatible
programs produce `tone_conflict`. Unknown sounds are compatible only when their
identities agree. Such a Hybrid is not certified; originals can be imported with
Hybrid disabled. This feature does not alter note selection to conceal a conflict.

## Evidence and checks

`import/tone-timeline.json` retains catalogues, original track identity, source
event index, performed occurrence, rational quarter position, score/audio times,
boundary decisions and the final original-arrangement schedules. The receipt
binds the original source bytes, packaged full audio bytes, recording duration
and actual timing-map parameters. Unrelated acoustic assessment diagnostics are
not timing-map inputs. The existing Hybrid receipt binds its completed chart.

The independent verifier reconstructs tone positions and states from the original
source and its separate score/recording clock implementations. Only deterministic
catalogue naming is shared with production. It compares complete schedules and
receipts, including intervals without attacks, and independently recomposes Hybrid
states and checks sustained-note conflicts. Archive mutation tests exercise lost
returns, shifted changes, wrong base states, missing receipts and stale bindings.
