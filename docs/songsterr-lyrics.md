# Songsterr authored lyrics

Song Browser imports one Songsterr-authored lyric stream into the existing
FeedPak `{t, d, w}` format. The game loader, display, highlighting and playback
clock require no changes. This does not use external lyric search, transcription,
or forced alignment of singing to audio.

## Source selection and interpretation

The importer retains every acquired track and reads lyrics independently of the
selected guitar/bass arrangements. It chooses the first lyric-bearing track
marked `withLyrics`, otherwise the first lyric-bearing vocal track, otherwise a
single unambiguous lyric-bearing track. All candidates and the selection rule
are recorded. Backing vocals are not interleaved with the selected stream.

Modern `newLyrics` use Songsterr's primary row: row zero for up to five rows, row
five otherwise. The one-based `offset` is the starting written measure. Syllables
attach to voice zero. Rests and grace notes do not consume syllables; ties do not
consume the next syllable; consecutive spaces can consume empty note slots.
Hyphens join syllables, underscores extend a preceding contiguous syllable, and
source `+` characters become spaces. Comments in brackets and parentheses follow
the public player's lexical rules. Explicit line endings are retained as phrase
hints. Lyrics that exceed the public player's text limits are omitted with a
reason rather than silently truncated.

When revision metadata advertises legacy lyrics and a designated part lacks
modern text, acquisition also attempts the revision's `lyrics.json` sidecar.
The same host/identity validation, cancellation, timeout and byte limits apply.
A missing or malformed sidecar is nonfatal. Legacy syllables are accepted only
when their written beat positions match the designated track exactly; overlapping
syllables, lyrics on rests and unmatched positions are unsupported. Legacy
support has synthetic coverage; the real-song corpus below uses modern lyrics.

This version supports the Songsterr JSON acquisition route. Lyrics embedded in
an uploaded Guitar Pro editor copy are not imported by this change.

## Timing and existing display contract

Written syllables pass through the same performed measure traversal and tempo
clock as instrumental notes, including repeats, alternate endings, swing and
tempo changes. Tied/underscore continuations stop at rests and navigation jumps.
Both the start and end of each syllable pass through the final recording
alignment, including its piecewise anchors and prepared-audio padding.

Events are clipped to the recording bounds. The importer does not extrapolate
beyond an available alignment map or invent timestamps from character counts.
Invalid, overlapping or collapsed intervals omit the optional lyric stream with
a recorded reason.

The output is sorted `{t, d, w}` in recording seconds. `-` joins syllables and `+`
terminates phrases as the current game expects. Source line endings, punctuation
and gaps of at least 1.25 seconds terminate phrases. When those are absent,
phrases break at complete word boundaries after eight words, 56 characters or
eight seconds. These are grouping hints for the existing renderer, not changes
to its presentation behavior. Language remains `und` because it is not inferred.

## Retained evidence

Preservation contract 79 adds `import/lyrics.json` for Songsterr JSON imports,
including imports without usable lyrics. The report records source/audio/map
hashes, candidates, selected track, source coordinates, performed spans, recording
spans, clipping/omissions and export status. The original source is retained.
`lyrics.json` and the existing manifest lyric fields are emitted only when usable
events exist. Unsupported/unavailable lyrics and unused trailing syllables
produce nonblocking conversion warnings.

The final-archive verifier independently traverses repeats and computes score
and recording clocks. It checks source selection, hashes, final text, intervals,
phrase endings and omissions. Lexical interpretation and phrase policy are
shared with production and are qualified separately against the public player;
the verifier is not an independent test of those shared policies.

`acousticAccuracyAssessed` is always false. Successful verification establishes
faithful conversion of the source lyrics onto the selected recording's alignment.
It does not establish that the tab author placed every syllable correctly against
the singing, nor that the selected recording matches an alternate performance.

## Validation references

- Public player bundle inspected on 2026-10-04:
  <https://static3.songsterr.com/production-main/static3/latest/common-DZB6Axu-J3KgDXP0.js>
  (SHA-256 `50bb5cd2fefe0bc4ea02cc5af0cbec205571e4df56031f09d92975be6de87d85`).
- `tests/fixtures/songsterr_lyrics_reference.json` retains expected beat assignments
  for 12 synthetic syntax cases and 150 seeded randomized cases. The fixture
  contains synthetic text, not Songsterr's implementation or real song lyrics.
- A local comparison additionally covers 26 real vocal tracks: all 188 cases
  matched the public player's syllable placement.
- Package tests cover timing, repeats, clipping, source selection, malformed
  optional data, legacy positions and deliberate archive corruption.
- Real-song validation and unchanged game-loader/renderer results are recorded
  outside the checkout in `verification/songsterr-lyrics-20261004` in the paired
  workspace. User library archives are not overwritten by these checks.
