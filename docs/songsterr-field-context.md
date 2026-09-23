# Conditional source metadata

`tempo.visible` is a boolean engraving preference, not a tempo enable flag.
Songsterr's public tempo-ramp expansion creates intermediate events with
`visible:false` and plays them. FeedForge retains this preference with a
display limitation while applying the explicit tempo unchanged. Non-boolean
values remain invalid in both the parser and independent reader.

`harmonicFret` alone is not an active harmonic. The public Harmonics performer
tests the presence of `harmonic` or `harmonicData` plus a fret before applying
pitch changes; its chord analyser likewise reads touch positions only inside
the harmonic branch. Retained finite nonnegative touch metadata with both
activation fields absent/null may accompany an ordinary note. This does not
relax the handling of any active harmonic type or omit its sounding pitch.

Evidence: public common bundle `common-z7xLi0BiPF1hudTP.js`, captured 23
September 2026, and `FluidsynthAudioPlayerWorkerEntry-BZJ7IhMqUXKWqzD-.js`,
SHA-256 836b1142b667812b6ff10d83e819018ab3e3823ce66ea33998d9f10e8f570b6e.
Source fields remain in the immutable original; conversion and verification
use separately implemented checks. Neither mapping changes source bytes.
