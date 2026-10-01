# Songsterr compatibility development

The released converter continues to use its own implementation and independent
package verifier. This development tool compares it with a reviewed capture of
Songsterr's scheduler. It never downloads or runs player scripts during an import.

## Qualified scope

184 synthetic cases cover strum/grace allocation, opening grace, ties, repeats,
alternate endings, swing, tuplets, legacy strum precedence and selected
combinations. Preparation clocks, source membership, string, fret, tie identity,
occurrence, authored attack and endpoint are checked. Extraction is qualified
against the complete captured script, including the pre-tie scheduling stage.

Six cases intentionally remain blocked: grace allocation consumes a strum
member's duration. Matching preparation or a hidden reference note is not
authorization to omit an attack. That policy still needs a decision.

Event comparison is qualified for these timing examples. It is not complete
equivalence for harmonic transformations, generated trill events, all voices,
synthesis, original-recording alignment or a finished FeedPak. Corpus preparation
replay and independent package verification are separate checks.

The first scheduling observation precedes let-ring, ties, staccato, automatic
strumming and instrument envelopes. Reference strum flooring is calculated
explicitly from source parameters; it does not round our rational musical clock.
Reference release is one tick before the authored end. Only binary floating
representation uses a 1e-8 tolerance in the compared unit, never musical timing
or missing-note tolerance.

Whole-measure rests have a separate, already established projection: their
silent span fills the meter while the captured preparation keeps the 1/1 glyph
duration. The comparator records this difference only for a single undotted
whole-rest beat with no pitched notes and the exact source meter. Wrong meter,
ordinary rests, and note timing differences are never covered by this exception.

## Commands

With the normal repository Python development dependencies installed:

```sh
npm run test:songsterr-compatibility
python -m tools.songsterr_compatibility catalog --output /new/path/catalog.json
python -m tools.songsterr_compatibility manifest --imports /original/imports --evidence /durable/evidence --output /new/path/corpus.json
python -m tools.songsterr_compatibility corpus --manifest /new/path/corpus.json --worker /reviewed/capture.js --node node --output /new/path/replay.json
python -m tools.songsterr_compatibility matrix --worker /reviewed/capture.js --output /new/path/matrix.json
python -m tools.songsterr_compatibility matrix --worker /reviewed/capture.js --reduce-failures --output /new/path/reduced-matrix.json
python -m tools.songsterr_compatibility compare --before /earlier/report.json --after /new/report.json --output /new/path/drift.json
```

Offline expected traces come from the pinned reference, not converter snapshots.
Mutation checks change clocks, membership, string, fret, ties and occurrence.
CI runs synthetic checks and the independent package-verifier suite only.
Field/guard inventory reuses current declarations and explicitly lists unmapped
items. Recognized fields remain conditional, not universally supported.

Corpus manifests check evidence and source hashes, song and revision. Later
retries form a separate manifest; missing sources remain visible. New reports
refuse to overwrite old ones. Exit 3 means a difference; exit 4 means incomplete
evidence, including reference checks not run. Rendered source never means a
successfully acquired, aligned, packaged or playable song.

`--trace` retains native scheduling stages. Keep real songs and large traces in
local verification evidence, not Git. The bounded reducer only deletes measures
when a supplied predicate still reproduces the failure. It refuses navigation
and automation contexts it cannot safely reduce; it does not repair fragments.
With `--reduce-failures`, unexpected synthetic stage differences automatically
receive bounded reduction and a final check against both implementations.
Expected blocked policy cases are not treated as new musical mismatches.

Reports record the asset/manifest digest, profile, Python/Node versions and
implementation digest. Compact reports retain signatures of prepared beats,
traversal, authored events, final reference events and each evaluator's output.
`compare` detects changes even when note counts stay identical. It requires
the same source identities and case set. Missing stages and older reports
without signatures are incomplete evidence, never an unchanged pass.
An unchanged comparison describes the recorded scope only; it does not qualify
new techniques or approve a new reference version.

When one arrangement is unsupported, the development audit also evaluates the
preparation of independently readable arrangements. The complete import stays
blocked under the existing all-requested-arrangements publication policy.

## Updating the reference

The accepted asset hash, structural binding locations, binding digests, options
and qualification scope are in `tools/songsterr_compatibility/reference-manifest.json`.
No player implementation is bundled or checked into Git. The authored profile
uses fluidsynth with RSE, JSON autofix and humanization disabled. Player defaults
can be inspected separately; they are not the deterministic acceptance profile.
The original-video cursor is a separate reference target.

Explicit maintenance uses `extract-reference.cjs capture.js candidate.json`
with the Babel versions already present in the repository's locked development
dependency graph. The tool checks their exact versions against the reviewed
reference manifest and records them in each candidate. It does not change or
install the app's dependencies. When reusing a validated external dependency
checkout, set `NODE_PATH` to that checkout's `node_modules`.
It creates a new candidate, never
updates the accepted manifest. Review changed bindings/options in a branch, run
`qualify-reference.cjs capture.js cases.json new-report.json`, then compare old
and new outputs on identical synthetic and retained sources. Review semantic
differences before changing expected traces. Never regenerate them from our
converter just to make a test pass.

Only the exact accepted digest executes. Node has a memory ceiling; VM calls
have deadlines. The VM is not an OS security sandbox: only reviewed code is
allowed. No app profile or credentials are supplied, and qualification forbids
network/audio-engine requests. Unknown hashes and adapter errors cannot pass.

## Remaining coverage

Complete pitch-effect correspondence, generated-event accounting, all historical
field forms, reduction across navigation/automation contexts, and browser cursor
qualification remain separate work. This matrix does not prove every possible
combination. Existing durable source evidence and per-arrangement diagnostics
continue to preserve and report unknown active music.
