# SNG bend time conversion

SNG bend point times are absolute song seconds. Feedpak `bnv[].t` is seconds
relative to the containing note/chord. Conversion subtracts the note onset once;
it never guesses a different coordinate system for individual points.

The source curve is interpreted as piecewise linear with held endpoint values.
Only its intersection with the note's sounding interval is emitted:

- Segments before onset are interpolated at onset; later points keep their
  original relative times. The source curve is not shifted to make its first
  point coincide with the attack.
- A curve entirely before onset is an exceptional source case. Its last authored
  value is held from onset. If that value is zero, no phantom scalar bend remains.
- Segments beyond sustain are interpolated at note end. The note is not extended
  and a release is not added.
- Conversion reports these boundary adjustments in its warnings. A zero-duration
  note has a zero-length sounding interval. Unsorted or nonfinite point arrays
  are rejected rather than silently reinterpreted.
- `bn` is the peak of the emitted curve in the sounding interval. Valid in-window
  curves, including positive-onset prebends, keep their authored shape.

A single positive authored target is retained. When it occurs after onset, an explicit
`(0, 0)` point supplies linear interpolation from the unbent onset to that target;
this support point is not claimed to be a recovered source event. The target's
time/value are retained, and its value is held afterward rather than inventing a
release. A target exactly at onset is a held prebend. An earlier target follows
the exceptional pre-onset policy above. Scalar bends with no source points stay
scalar; their missing timing is not reconstructed.

A lone zero-valued target combined with a positive declared peak is ambiguous
about its initial trajectory. Conversion preserves the declared `bn` and literal
zero target, reports a warning, and does not infer a prebend or a release path.

`feedback_converter.bend_curves.normalize_sng_bend_curve` is a small standard-
library-only adapter shared with source-assisted repair workflows. Its adjustment
categories are descriptive; they do not authorize heuristic repair of a Feedpak
whose source coordinate system is unknown.
