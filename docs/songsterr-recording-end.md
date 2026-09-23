# Recording-end cutoff (preservation contract 11)

When new tab attacks fall past the selected recording, the importer may omit
them only after checking earlier recording timing. This extends the contract 10
held-tail policy in `songsterr-terminal-sustains.md`; it never substitutes audio,
stretches the song or changes earlier attack positions to obtain a pass.

Eligibility requires the accepted Songsterr map for the exact song, approved
revision and selected video. The audio end must be inside the final performed
bar, with at most three seconds left in a bar no longer than eight seconds.
Larger missing sections remain failures. A candidate map cannot build a FeedPak
until the acoustic check supports it. An inconclusive check stops the import
with an explanation, without falling back to an estimate to bypass the check.

## Acoustic evidence and its limits

The check compares expected pitched notes and attacks with the decoded full
recording in overlapping 16-second windows, eight seconds apart. At least three
active windows are needed, and every active window must contain supporting
pitch and onset evidence in at least one guitar/bass part. Sparse, silent,
ambiguous or mismatched regions cannot grant approval. Individual parts may
remain inconclusive when another part supports the shared song clock.

Pitch evidence uses harmonic spectral contrast at 11025 Hz, with a roughly
10 ms analysis hop. Scores at the authored timing are compared with nearby
positions and offsets out to three seconds. Pitch integration can peak within
120 ms of a sample inside a ringing note; attack evidence independently requires
a local peak within 80 ms of the authored attacks. The map is never adjusted.
All thresholds and per-window, per-part metrics are versioned and retained.
Ranks and ratios are engineering checks, not calibrated probabilities. This
supports the shared recording timing; it cannot prove every note, instrument,
source transcription or expressive gesture is correct. Repetitive music can
be ambiguous. This is deliberately a conservative final-bar policy rather
than permission to truncate arbitrary mismatched songs.

## Preserved data and verification

Every string-note whose mapped attack is at or after the audio end is omitted
from playable arrangements and recorded in `import/ending-omissions.json`, with
track, string, fret, original mapped attack and duration. Earlier held tails use
the existing sustain ledger. Unfinished pitch gestures crossing the boundary,
partial staggered chords or removal of an entire arrangement still stop the
import. All earlier attacks and techniques must remain unchanged.

Original source bytes and written notation stay intact in the FeedPak. The
versioned `import/recording-sync.json` records acoustic evidence bound to the
exact packaged audio hash and timing map; the omission policy hashes that
report. Final verification independently reconstructs notes from the raw source,
recomputes allowed omissions, compares the ledger and every remaining note,
checks actual audio duration, and reruns the acoustic check on packaged audio.
A saved or forged successful assessment cannot by itself pass verification.

The completed import displays the omitted note count and retained original.
Contracts 1–10 remain readable as historical evidence but cannot authorize new
publication/reuse under contract 11. No game or plugin changes are required.

## Validation

Synthetic plucked-note recordings provide known timing/pitch truth, including
wrong offsets, drift, wrong notes, silence and sparse input. Corruption tests
cover early notes, missing notes, omission ledgers, identity, old contracts and
forged successful evidence for unrelated audio. Real-song acceptance uses the
four captured approved revisions and their original selected recordings (Money,
Every Breath You Take, Sunshine Of Your Love and Billie Jean), with deliberate
offset/drift negative controls. Test artifacts stay outside the source checkout.
