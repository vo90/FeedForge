# Let-ring compatibility reporting (contract 13)

Songsterr's `letRing` is retained as `lr` in chart/notation data and in the exact
original source. The present game note loader does not retain or display the
chart flag. The converter preserves written/tied note durations; it does not
reproduce Songsterr's additional synthesized ringing across subsequent beats.

Imports now declare this as a nonblocking display/expression limitation at each
authored beat, including its arrangement and source coordinates. This is a
disclosure correction, not completed let-ring support. It neither lengthens
notes nor replaces the marking with `ln` (which means a linked next note).
Pitch, attacks, source durations and all other techniques remain verified.

The independent verifier requires every active guitar/bass let-ring marking in
the compatibility report, rejects missing/altered reports and still rejects
changed notes. Contract 13 prevents an older report being treated as the current
complete assessment. Historical reports remain readable. Inactive false flags
and parts outside guitar/bass scope do not acquire this warning.

Future work must decide how to present ringing intent, whether to distinguish
written length from ringing duration and how that affects sustain judgments.
The source player's extension, same-string cutoff, tie and staccato ordering
provide reference evidence, but silently changing gameplay sustain lengths is
not part of this reporting fix.
