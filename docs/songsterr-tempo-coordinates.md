# Tempo positions use static ticks

Songsterr's tempo/fermata automation positions use 960 static ticks per quarter.
They are distinct from beat duration fractions, which use whole-note units.
For example, tempo position 1920 means quarter 2; numeric 0.5 means half a static
tick, not half a whole note. The previous conversion incorrectly multiplied
positions by four, rejecting the captured Free Bird tempo in a 2/4 bar.

The importer follows the current public playback coordinates, including small
fractional values. It does not guess that a legacy author intended another unit.
Original automation data is retained. Enabled ramps can use explicit interior
positions; generated BPM steps retain the existing authored rational clock.

Evidence captured 2026-09-23:
- Public common-z7xLi0BiPF1hudTP.js computes a tempo tick using
  totalTicks × position / measureLengthAt960.
- The public audio worker's Hr/Ur/Kr functions add tempo position to static
  measure offsets and divide ramp spans by 960 to count quarter-note steps.
- Worker SHA-256: 9bc2e262f42077e5f6d13d7c67269d8e92c2e24f8d6ff47ca9601b235251c33f.
- Common SHA-256: 039a95156a0b966e7c3602ea8d0f4ae403266ec2f7ba930dd64fcbe41151fc29.
