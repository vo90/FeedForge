# Hybrid Lead section identities

Section descriptions do not establish a guitarist's identity. In particular,
`Solo (Part 2)` must not require the generated arrangement `Voice 2`.

The builder and independent verifier interpret parenthesized source labels
using these rules:

- Arabic numbers, ordinals, Roman-only labels and section/voice/track identifiers
  are not named-soloist evidence. Letter suffixes such as `Part A` are labels too.
- Generic roles, sounds and section descriptions (for example `Lead Guitar`,
  `Wah`, `Talkbox`, `Intro`) alone do not name a performer.
- An actual name alongside a descriptor remains usable: `Adrian Smith - Part II`.
- Ampersands, slashes, commas and `and` separate parallel named players. Unicode
  names, apostrophes, hyphens and initials remain usable for matching.
- Without a usable named identity, the existing musical evidence, dedicated
  solo rules and coherent-voice selection still apply. This is not permission
  to remove notes or automatically accept an unverified package.

The verifier implements source-label interpretation independently; it does not
trust the builder's chosen owners, role map, or coverage claim. Tests exercise
both numbered-label false positives and genuine named-player omissions.
Unrecognized free-text descriptions are not guaranteed performer identities;
they must still match an active source name. This change does not introduce a
general natural-language understanding of section labels.

No chart schema, note data, timing, scoring or archive migration changes are
required. Existing imported songs are not rewritten.
