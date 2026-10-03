# Chord-aware generated hand positions

`generated_hand_positions.py` is the canonical pure position generator shared
with FeedBack's load-time compatibility adapter. Keep the game copy byte-identical
and include `generated_hand_positions.LICENSE` with either distribution.

The position policy is `chord-local-v1`, recorded alongside the existing
`feedforge-chart-guidance-v2` receipt and in the Songsterr import recipe. This
changes presentation anchors, not musical events or the archive schema. Ordinary
finalization preserves a valid older receipt; explicit regeneration upgrades it.
Unknown policies and edited generated guidance are rejected for review.

At a chord attack, prefer its lowest fretted note as the first fret in a minimum
four-cell position. Widen only to cover sounding notes and known slide corridors.
If a lower overlapping sustain prevents that position, cover the sustain until
its release and then move to the chord's preferred position. Do not carry a wide
span through three later attacks or a minimum one-second delay. An open-only
attack returns to four contextual cells. At the physical neck end, clamp the
position to retain four valid fret cells.

Single-note passages keep stable positions while those positions cover the
material. The bounded preview and compact explicit hammer-on/pull-off reservation
remain intact. Untimed or ambiguous relationships do not invent legato links.

For the reported Rats passage, the 3-to-8 slide ends at 105.78625. The following
positions are 7–10 at 105.78625, 3–6 at 107.03 and 5–8 at 107.75375. A separate
renderer rule gives each chord its own shape-local box even if overlapping notes
legitimately require a wider lane.

Regression coverage is in `tests/test_chart_guidance.py`. The FeedBack companion
audit checks canonical-copy parity, independent coverage, and source/archive
immutability across a read-only library. The implementation was audited against
64 arrangements in 14 songs, with 50 receiving changed lanes.

All 82 guidance tests passed. The full suite passed 4,424 tests with five skipped;
one CLI subprocess lacked the isolated checkout on its import path. That final
end-to-end test passed when rerun with `PYTHONPATH` pointing to this checkout's
`src`. Use the prepared build Python environment, including the existing
`yt-dlp-getpot-wpc` distribution, for the full packaging suite.
