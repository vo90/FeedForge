# Section annotations, preservation contract 18

The complete source envelope determines section labels, regardless of a
diagnostic track selection. One distinct nonempty label among eligible guitar
and bass tracks takes precedence over excluded instruments' labels. When those
tracks supply none, one distinct other-track label can be used. Ambiguity in
either selection remains a located error except for the equivalent-label rules
below; names are not invented or combined.

Equivalent labels at the **same written measure** can differ in case/whitespace,
or use a bare conventional section name alongside one positive Arabic-numbered
version: `Verse` with `Verse 1`, for example. The accepted bases are Intro, Verse,
Pre-Chorus, Chorus, Post-Chorus, Bridge, Interlude, Solo, Outro, Ending, Break,
Breakdown, Riff and Hook (case insensitive). Every numbered candidate must have
the same number and every candidate the same base. No Roman-number, free-text,
performer-name or musical-role suffix is discarded. `Verse 1` versus `Verse 2`,
`Verse` versus `Solo`, and `Solo (Kurt)` versus `Solo (Krist)` remain conflicts.

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
