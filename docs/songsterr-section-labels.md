# Section annotations

Section provenance is retained and independently checked since preservation
contract 18; the current importer also applies the majority policy below.

The complete source envelope determines section labels, regardless of a
diagnostic track selection. One distinct nonempty label among eligible guitar
and bass tracks takes precedence over excluded instruments' labels. When those
tracks supply none, other-track labels can be used. Equivalent labels use the
existing normalization below. Meaningfully different names use the approved
majority and source-order rule; names are not invented or combined.

Equivalent labels at the **same written measure** can differ in case/whitespace,
or use a bare conventional section name alongside one positive Arabic-numbered
version: `Verse` with `Verse 1`, for example. The accepted bases are Intro, Verse,
Pre-Chorus, Chorus, Post-Chorus, Bridge, Interlude, Solo, Outro, Ending, Break,
Breakdown, Riff and Hook (case insensitive). Every numbered candidate must have
the same number and every candidate the same base. No Roman-number, free-text,
performer-name or musical-role suffix is discarded. `Verse 1` versus `Verse 2`,
`Verse` versus `Solo`, and `Solo (Kurt)` versus `Solo (Krist)` are distinct names
for voting, not equivalent spellings.

The chosen label is the numbered authored string when available. Formatting
ties prefer fewer redundant whitespace characters, then casefolded and literal
lexical order, independent of track order. The exact selected string and all
original labels are retained. Provenance distinguishes `guitar_bass_equivalent_labels`
and `other_tracks_equivalent_labels` from exact consensus. Excluded instruments
cannot override playable-track labels. Diagnostic track selection cannot change
the result. Section boundaries, notes, timing and named Hybrid Lead ownership
are unchanged; this rule does not reconcile different sections across time.

Raw source remains unchanged. `song_import.sourceMetadata.sectionLabels`
records written measure, chosen label, selection basis and each track's label,
identity, eligibility and source location. The independent reader derives
those decisions separately, checks both the published sections and provenance,
and continues checking notes, navigation, meter and tempo. Older evidence and
cached successes cannot certify this version without fresh verification.

Tests cover the frozen Enter Sandman/Beat It label patterns, source immutability,
partial diagnostic selection, fallback, malformed annotations, actual musical
conflicts, and altered package sections/provenance. Equivalent-label tests also
cover number conflicts, arbitrary/named suffixes, input/track-order preservation,
unchanged note events and independent package verification. No schema or
preservation-contract bump is needed: already-convertible inputs keep their
decisions, while previously rejected inputs must pass fresh verification.

## Majority selection for differing names

At each written measure, count each labelled original guitar/bass source track
once. The most common name wins. In a tie, choose the tied name appearing first
in source track order, not the first annotation regardless of its vote count.
Case/whitespace variants count together and the selected text is an authored
spelling chosen by the existing formatting rule. Unambiguous equivalent names
such as `Verse`/`Verse 1` retain their existing treatment before voting.

Excluded instruments cannot outvote a guitar/bass label. If no guitar/bass
track has a label, the same vote and tie-break apply to the labelled fallback
tracks. Empty labels do not vote. Diagnostic track selection, repeated visits,
split voices and generated Hybrid Lead arrangements cannot add or remove votes:
the decision is made from the complete original source before projection.

Provenance uses the `guitar_bass_` or `other_tracks_` prefix with
`majority_label` or `track_order_tiebreak`. Every original annotation, including
minority and excluded-instrument labels, remains in `sectionLabels` and the
original embedded score. The independent reader recounts raw-source votes and
checks the selected section, basis and full provenance in the package. Import
warnings explain the choice. Malformed markers and actual meter, tempo or
repeat-navigation conflicts remain errors. No FeedBack game change is needed.
