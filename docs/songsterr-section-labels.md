# Section annotations, preservation contract 18

The complete source envelope determines section labels, regardless of a
diagnostic track selection. One distinct nonempty label among eligible guitar
and bass tracks takes precedence over excluded instruments' labels. When those
tracks supply none, one distinct other-track label can be used. Ambiguity in
either selection remains a located error; names are not invented or combined.

Raw source remains unchanged. `song_import.sourceMetadata.sectionLabels`
records written measure, chosen label, selection basis and each track's label,
identity, eligibility and source location. The independent reader derives
those decisions separately, checks both the published sections and provenance,
and continues checking notes, navigation, meter and tempo. Older evidence and
cached successes cannot certify this version without fresh verification.

Tests cover the frozen Enter Sandman/Beat It label patterns, source immutability,
partial diagnostic selection, fallback, malformed annotations, actual musical
conflicts, and altered package sections/provenance.
