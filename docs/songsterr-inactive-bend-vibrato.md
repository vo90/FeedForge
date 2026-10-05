# Legacy inactive bend-point vibrato

Preservation contract 86 recognizes `note.bend.points[].vibrato` when absent,
null, false or numeric zero. Older approved guitar and bass revisions include
this inactive field on otherwise ordinary bends. It remains in the original
source and feature inventory as source-only metadata. It adds no vibrato,
warning, note, duration or pitch change.

This exception applies only to the bend-point field. Ordinary note-level
vibrato, timed vibrato marks and bend coordinates keep their existing rules.
True and nonzero values remain unsupported; strings, containers and nonfinite
values remain invalid. The parser and capability inventory use a dedicated
validator; the independent source reader checks the rule separately.

The reviewed player reference has SHA-256
`4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab`.
`tools/songsterr_compatibility/inactive-bend-vibrato-reference.cjs` generates
54 synthetic guitar/bass cases across authored and player-defaults profiles,
ordinary bends, real note vibrato and tied bends. Full native preparation and
synthesis traces match omission of the inactive field. The committed fixture
contains only synthetic music; the player is not included or shipped.

Regression tests compare the converter and independent reader to that reference,
check strict invalid/active values, preserve the exact source bytes in FeedPaks,
and detect corrupted bend, vibrato, timing and notation data. Archive verification
also runs with Hybrid Lead enabled.

This qualifies the inactive legacy field only. Other unsupported source fields,
recording retrieval and audio synchronization still require their own checks.
