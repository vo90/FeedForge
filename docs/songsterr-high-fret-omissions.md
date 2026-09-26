# High-fret omission implementation and test plan

Approved scope: import valid Songsterr guitar/bass tabs without requiring gameplay
above fret 24. This is an explicit partial chart, not a faithful playable conversion
of the omitted passages. Original source bytes remain inside the FeedPak.

1. Develop on `fix/songsterr-high-fret-omissions` from the consolidated
   `integration/songsterr-test` baseline dcc0cdb. Do not modify PSARC conversion.
2. Resolve authored ties, repeats and gestures normally. At package creation omit
   performed notes at frets 25–48, and whole slide events with destinations in that
   range. Do not transpose, refinger, invent bends, or turn slides into false holds.
   Existing unpitched-mute and visual-scrape representations remain exempt.
3. Keep supported chord members; rebuild templates and turn single-member chords
   into single notes. Clear outgoing link flags whose next event was omitted.
   Preserve remaining pitches, techniques, attacks, sustains and timeline.
   Omit fully affected arrangements, explicitly recording them; fail if none remain.
4. Retain a source-hashed omission receipt with each performed position, reason,
   removed link and excluded arrangement. Withhold staff notation for affected
   arrangements (the complete notation remains in the original tab) so it cannot
   contradict playable charts. Tab View continues to use the playable chart.
5. Independently reconstruct allowed omissions from the raw source, check the
   receipt and every remaining event, and keep strict FeedPak validation. Bump the
   preservation contract/cache version. Expose omitted-note counts in app history.
6. Test boundaries, ties, repeats, mixed/all-high chords, slides in both directions,
   links, nonlinear timing, unchanged unaffected data and corrupt/missing receipts.
   Replay the frozen 100-song corpus; distinguish fully retained charts, charts with
   omissions, and remaining failures. Build/import representative real packages.
7. Merge the verified fix into the shared Songsterr integration branch. Use the one
   shared test environment for combined game checks; never create a runtime per fix.
   Keep source/evidence, logs, libraries and persistent profiles. Any runtime migration
   must use the declared Electron 44 sources and a matching combined native addon.

Invalid source and unrelated unsupported techniques remain errors. Source/audio
synchronization is assessed against the original performance, before omissions.
Omissions do not authorize shortening audio or relaxing synchronization checks.
