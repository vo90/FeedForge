# Tempo entries sharing a position

Contract 46 follows the reviewed Songsterr preparation rule `oi/si`: the last
complete instruction at an exact written measure/tick coordinate supersedes
earlier entries within that part. Resolution precedes fermata and gradual-tempo
expansion. BPM, note unit, dotted flag and linear flag all belong to the selected
instruction. An earlier linear flag must not leak into its replacement.

Every raw entry is validated before selection. Nearby positions are never merged,
BPM differences have no tolerance, and differing clocks across parts still fail.
Missing-measure automation remains a separate unsupported case. No note, pitch,
attack, tie or source relationship is repaired or omitted by this rule.

The complete source list is retained. Each superseded entry has a located
`tempo.superseded` finding naming its original value and final selected entry.
The independent verifier performs its own reverse selection, checks the resulting
clock, and independently derives the required diagnostic values from source.
Earlier report versions do not require this new diagnostic; their musical output
is still checked. Contract 46 cannot use an older report to skip this accounting.

The development reference is pinned to worker SHA-256
`4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab`.
`tools/songsterr_compatibility/tempo-precedence-reference.cjs` records 24 synthetic
cases covering both scheduling profiles, repeats, holds and replacement of linear
flags/units. It compares the complete scheduler and emitted tempo events against
controls without superseded entries. Repository tests consume those known answers;
regeneration is separate and never derives expectations from the converter.

This establishes source clock interpretation, not acoustic alignment to every
note in an original recording.
