# Timed wah guidance

Preservation contract 112 retains Songsterr's explicit beat `wahwah` values
(`open` / `closed`) as arrangement `wah` events. Each contains recording time
`t`, `state`, physical low-to-high `strings`, original beat `source_id`, note
`source_ids`, and one-based performed-measure `occurrence`. Empty strings/IDs
are valid on rests. Absence means no instruction, not pedal bypass.

The event clock follows performed repeats, written tempo, recording alignment,
and preparation silence. Events in a discarded opening or after the accepted
recording cutoff are omitted. The source archive is retained. Hybrid Lead uses
the selected source-note lineage, with rest instructions owned by its selected
passage. Same-time duplicate source instructions remain in the archive and are
coalesced only for display. Conflicting instructions display both symbols.

The independent verifier reconstructs original events from raw beat facts and
its own repeat/recording clock. It separately verifies Hybrid selection from
the checked original charts and passage receipts. Contracts before 112 retain
their historical source-only limitation; they do not claim visible guidance.

The updated game displays `WAH ○` / `WAH +` above the musical position. A
highway setting can hide these optional cues. Tabview uses GP5's standard wah
symbols, including cues on rests and ties. No audio processing, automatic
effect switching, pitch targets, new attacks or scoring obligations are added.
Neither the exporter nor renderer interprets these symbols as a DSP sweep or
an on/off pedal switch.
