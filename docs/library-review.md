# Library result review

Library audit results can be searched by artist, song, album, year or filename,
then filtered to All, Duplicates or Needs attention. All includes healthy files.
Results are ordered by artist/title and paged in groups of 50. Search also limits
the duplicate groups; when one file matches, the whole group remains visible for
comparison.

Duplicate checking remains off by default. When enabled, its default comparison
still uses normalized artist/title/album/year and duration rounded to five
seconds. The optional **Artist and title — review all versions** mode restores
broader review across releases. It identifies candidates to compare, not proof
that recordings or charts are equivalent. Live and acoustic qualifiers remain
distinct. Missing artist/title and unreadable files do not form groups.

Run audit after changing the scan criteria. Displayed groups describe the last
completed scan. Per-file release metadata and duration are shown when comparing
versions. Suggested keeps continue to favor arrangements, stems and credits.
Nothing is selected for removal automatically. Removal retains the confirmation
and existing exclusive-mutation API; successful removals trigger a fresh scan.
Changing the search or view clears removal selections.

Focused checks:

```
node --test tests/js/library-audit.test.cjs ui/src/library-review.test.mjs
```

These use synthetic rows and do not read or change a song library. The conversion
scheduler, inspection worker limits and memory pause behavior are unchanged.
