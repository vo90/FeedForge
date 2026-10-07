# Pending slides across plain tied placeholders

Preservation contract **96** adds a separate, source-bound plain-tie identity
rule: `plain-tie-keeps-pending-slide-attack-target`.

A literal shift/legato instruction on the original pitched attack survives one
or more hidden plain tied entries on the same voice/string. Their stored fret
may differ from the held target. Those entries keep the held fret in gameplay
and notation; the slide still targets the next real fresh attack. Unlike HP,
the hidden tie does not consume the slide instruction. No attack is invented,
and a genuine gap between release and the later attack remains a gap.

Qualification requires unique source slots and origin identity, literal source
markers/ties, exact continuous held history, and no competing link or new
controller. Initial explicit slight note vibrato is retained, including the
duplicate beat marking already handled by note ownership. Bends, incoming
HO/PO/legato compounds, harmonics, mutes, slide-in/out, displaced/staccato
attacks and continuation-owned expression retain their guards. Once a differing
placeholder qualifies, later equal-fret continuations are checked too. Existing
equal-fret-only behavior is unchanged. A missing real slide destination remains
an error.

The existing `import/plain-tie-identity.json` receipt retains raw/effective frets,
marker and held-origin identities, voice/string, occurrence and score clocks,
bound to the untouched source SHA-256. The independent evaluator reconstructs
the rule from source atoms without calling producer predicates. Builder,
compatibility inventory, verifier and Electron acceptance use contract96;
historical archives through95 retain their earlier semantics, including local
sync, synchronization and high-fret reconstruction routes.

Synthetic fixtures retain captured `ks` held clocks and `Ss` slide destinations
from the pinned complete Songsterr worker. They cover guitar/bass, shift/legato,
different placeholders, chains, versions, cross-bar and initial-vibrato cases.
Synthesis note-off shortening is not substituted for source chart duration.
Tests also cover repeats/tempo changes, ambiguous histories, source/receipt/chart
tampering, historical verification and immutable input models. No game renderer
or scoring change, audio-sync claim, or rewriting of existing FeedPaks is involved.
