# Boolean tremolo-bar markers

Some retained Songsterr beats contain a literal `tremoloBar: true` or `false`
instead of an object describing a pitch curve. The reviewed Songsterr performer
does not generate bar movement from either boolean. These values contain no
direction, amount, or timing from which to construct one.

The importer and independent source reader therefore treat only these literal
booleans as supplying no bar curve. They preserve separate bar vibrato and all
existing notes, harmonics, bends, and ties. In particular, they must not invent
a zero-valued curve: that would reset an existing held bend at a tied segment.
Valid curve objects keep their existing interpretation, and unsupported shapes
remain guarded. This rule applies to beat fields, not note fields.

Both values remain unchanged in the archived source. Compatibility inventory
records their exact value and location as `source_metadata` / `source_retained`,
including resting beats and excluded parts. This is diagnostic accounting, not
a player limitation. An excluded part is not made playable by retaining its flag.

Preservation contract and compatibility inventory 121 qualify this interpretation.
The independent verifier derives the expected rows directly from the source and
rejects omitted rows, changed types or values, fabricated curves, and downgraded
claims. Historical packages that never parsed an excluded part remain verifiable
without retroactively requiring the new row.

## Evidence and regression coverage

The retained public worker was compared with a reviewed extraction, including the
actual harmonic ties in Classical Gas and synthetic plain, vibrato, and held-bar
cases. Boolean, absent, and null fields produced identical musical events. A
neutral curve control produced different held-pitch behavior. This qualifies the
reviewed worker behavior; it is not a claim about every future Songsterr version.

`tests/test_songsterr_boolean_tremolo.py` covers both booleans, harmonics, held
curves, independent timing, malformed values, negative-fret mutes, excluded parts,
archive tampering, historical contracts, and Hybrid Lead retiming. Related whammy,
bar/bend, builder, and verifier suites protect the existing expression paths.

The original Classical Gas source passes this field interpretation but still
reaches a separate unresolved tie in bar 43. This change does not qualify that
tie or establish full-song import acceptance.
