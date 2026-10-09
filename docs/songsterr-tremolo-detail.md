# Tremolo picking detail — preservation contract 111

`tr` retains the legacy tremolo instruction. Optional `tremolo_marks` is an
authoritative ordered list of nonoverlapping intervals, bounded by `sus`.
Each interval contains `start`, `end` (seconds after the attack), `rate`
(reduced numerator/denominator of a whole note), `period` (seconds per pick
in the recording clock), `phase` (0 inclusive to 1 exclusive), and `mode`.
Specified intervals have a positive rate and period. Unspecified or conflicting
intervals use null rate/period and zero phase. An empty list means no active span.

Beat-level active rates take precedence over note-level rates. Instructions
continue through tied segments, including unmarked continuations. Later-only
instructions start at their own segment. Conflicting overlapping instructions
retain generic expression; no source rate is selected arbitrarily. Tempo changes
and recording-map slope changes split intervals without splitting attacks.
Source ownership tolerates only the existing six-decimal clock rounding.

The producer and independent verifier reconstruct spans from separate source and
clock models. Ending crops shorten the interval, Hybrid Lead retains its source
detail, and old preservation contracts omit this new extension. Original source
data remains in the archive. No attack, pitch, written rhythm, scored duration,
audio or scoring policy changes.

The updated highway indicates speed by curve spacing without a speed label.
Lateral width and existing visibility/narrowing behavior remain unchanged.
The fastest displayed spacing is the former generic 0.06-second pattern, to
retain readability. Faster authored rates remain exact in the package.
Tabview engraves supported eighth/sixteenth/thirty-second picking values and
uses display ties for changes within a sustain. Unknown/conflicting and GP5-
unrepresentable rates use a generic `trem.` instruction, never invented eighths.
Only qualified detail is informational; genuinely generic display remains a
grouped limitation.

Native scheduling qualification: captured Songsterr worker SHA-256
`4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab`.
Regression coverage includes source/beat precedence, tied ownership, repeat
occurrences, tempo/recording maps, ending crops, independent corruption checks,
Hybrid Lead, serialized GP5, highway width/phase/visibility and legacy fallback.
