# Source-preserving song imports

FeedForge converts the supplied approved score. It does not improve its musical
content: unusual parts, written pitches, tuning, and annotations retain their
source meaning. Converter validation checks representation and completeness,
not whether the transcription sounds correct.

## Representations

The import model retains source measures, voices, beats, rests, exact fractional
positions and durations, written rhythm, annotations and stable source IDs.
Playable notes are a separate projection: ties become sustained events and notes
from one authored beat may form a chord. Coincident independent voices are not
automatically merged. Ambiguous attacks on the same physical string fail with an
explicit explanation; identical notes are not silently deduplicated.

Chord templates contain the source string/fret shape. Missing chord names and
fingers stay unknown. The importer does not invent handshapes or fingerings.
Ghost notes remain pitched ghost notes, distinct from dead/muted notes. A
direction-only slide has authoritative `slide_out_marks` entries, each containing
`direction: up|down` and `start`/`end` seconds relative to the merged note attack.
The intervals preserve the written marked segments, including multiple tied
segments; they do not specify slide speed or a destination fret. Each boundary
is independently mapped through recording synchronization and rounded to six
decimal places. `slide_out: up|down` remains only when all marks agree in direction
for older consumers. A present marks array is authoritative, including an empty
array; consumers must not also render the scalar.

An incoming slide has authoritative `slide_in_marks` entries containing
`direction: up|down` (pitch motion) and `time` seconds relative to the merged
attack. Each point is the authored destination-segment onset, including a
marked tied continuation. It supplies no starting fret, duration or trajectory.
Points are independently synchronized and rounded to six decimal places; an
onset at zero is valid, even for a zero-sustain consumer note. Ordered distinct
points must remain distinct at this precision. An empty array means no incoming
cues. Invalid arrays are rejected, not replaced with guessed scalar data.
Incoming and outgoing markings can coexist; neither removes the other.

Songsterr's public schema uses `below` (upward pitch motion) and `above`
(downward), either alone or concatenated with `shift`, `legato`, `downwards` or
`upwards`. GPIF slide flags 16 and 32 describe incoming slides from below and
above, independently of outgoing flags 1, 2, 4 and 8. Conflicting/unknown flags
stay unsupported. These interpretations were checked against the
[Songsterr notation guide](https://www.songsterr.com/howtoreadtab#slideIn), its
public application schema, and the
[alphaTab GPIF reader](https://github.com/CoderLine/alphaTab/blob/develop/packages/alphatab/src/importer/GpifParser.ts).

The performance boundary includes:

- `tracks[].notes`, `chords`, and `templates`; chord children have their own
  sustain/techniques, and inherit their onset from the parent chord.
- `source_ids` linking events to authored notes, including tied continuations.
- `tracks[].notation` using FeedPak notation v1. Times and `end_time` initially
  refer to score seconds and are retimed alongside the playable chart. Written
  rhythm stays separate from performed duration. The playable fretted chart is
  authoritative for gameplay.
- `sourceScore`: a versioned full Songsterr document, or the exact GPIF XML bytes
  encoded as base64. This includes parts outside current fretted playback scope.
- `featureInventory`: aggregated source-field handling, counts and examples.
  `representations` distinguishes source-only fields from fields projected to
  playable charts, notation, or retained layout hints. Musical field inspection
  covers playable parts; excluded parts remain in the full source document.

Only downbeats have a positive, one-based measure number; other beats use `-1`.
The song time-signature map preserves signature changes. Source sections and
repeat visits share the same performed timeline as notes.

## Explicit limits

The importer does not claim support for every source feature. Active unfamiliar
musical fields, unresolved ties/links, unsupported navigation or timing, and
effects whose meaning cannot be mapped without guessing produce an explicit
error. Inactive unfamiliar flags and unfamiliar descriptive metadata remain in
source evidence and the inventory. Beat-level tapping and vibrato are recognized
as well as their note-level forms.

Written notation outside FeedPak v1's duration vocabulary is not quantized: the
playable event keeps its exact timing, the full source is retained, and that
track's notation sidecar is omitted with a warning. Lyrics, unsupported written
details, and non-guitar/bass parts remain available in source evidence; this is
not a claim that they are playable or displayed. Consumers may render fewer
notation effects than the format can retain.

The notation projection currently includes voices, rests, written rhythm,
tuplets, ties, note coordinates, dynamics, text, ghost/dead notes, vibrato,
accents, tapping, and resolved hammer-ons/pull-offs. Bends, slides, harmonics,
tremolo picking and links retain their playable representation and full raw
source; this adapter does not claim an engraved equivalent for those effects.
Authored key signatures stay in source evidence with an explicit warning.

The GPIF adapter and notation writer are FeedForge implementations. They use the
public data-format contracts, without importing or copying game converter code.

## Verification and saved evidence

Publication requires structural validation and an independent comparison of the
raw score with the archive. The independent checker has its own source reader,
repeat traversal and timing math. Its known-answer and mutation tests do not use
the production converter to produce expected values. A difference blocks saving;
unsupported checks report Needs attention. In particular, an omitted notation
sidecar currently prevents a fully checked import even if playable timing is exact.

Contract and verifier version 3, in recipe version 3, add independent incoming-slide
destination checks to the outgoing segment checks from contract 2. Version 1/2
reports remain readable but do not establish current timing fidelity, and old
outputs are not reused as version 3 imports.
History and recovery receipts bind
the successful check to the exact archive SHA-256. Old imports are not relabeled
checked. Later edits leave the original report intact and mark the current file
as modified. A matching filename is never evidence of an identical conversion.

The persistent `songsterr/evidence` store retains content-addressed source bytes,
the performed model and lineage, source synchronization, the complete applied map,
feature inventory, archive member hashes and verification report. It is separate
from the pruned download/attempt cache. Save conversion report exports a portable
ZIP without copying the original audio, browser account state or credentials.
Evidence is not silently deleted when the temporary download cache is cleaned.

The report separates source fidelity, source-map versus estimated audio timing,
and consumer capabilities. `ghost: true`, `slide_out: up|down`, and timed
`slide_out_marks` and `slide_in_marks` are additive
extensions, not a claim that every released game renderer understands them.
Their requirement is recorded in `song_import.compatibility`.

## Album artwork

MusicBrainz recording/album matching and Cover Art Archive front covers are
optional enrichment. A supplied album is respected; otherwise the resolver seeks
the original official studio album and keeps recording-version distinctions.
Ambiguous/truncated catalogue results, unavailable artwork and service failures
leave cover art absent. They do not block faithful musical conversion. A video
thumbnail or generated title card is never substituted for an album cover.
Imported covers use JPEG quality 90 with a maximum 512-pixel edge, preserving
aspect ratio without enlargement. Legacy PNG caches are compacted locally;
already compact JPEGs are reused without another lossy encoding generation.
An image can also be supplied afterward using the existing Edit FeedPaks controls.

The resolver uses a descriptive User-Agent, coordinates its one-request-per-second
MusicBrainz limit across worker processes, caches results and observes bounded
retries. Images are decoded, size-limited and normalized with aspect ratio intact.
Matched art is embedded for offline playback; its database IDs and hash are saved
in provenance. Missing album/year may be filled from a confident match, without
changing original musical content or overwriting conflicting source metadata.
