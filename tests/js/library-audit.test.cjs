const test = require("node:test");
const assert = require("node:assert/strict");
const { duplicateAuditKey, duplicateGroupsFromAuditRows, normalizeDuplicateMatch, normalizeDuplicateText } = require("../../electron/library-audit.cjs");

function row(overrides = {}, match = "strict") {
  const value = { filePath: "C:\\songs\\song.feedpak", relativePath: "song.feedpak", status: "pass",
    title: "Song", artist: "Artist", album: "Album", year: "2020", duration: 180,
    arrangements: 1, stems: 1, stemIds: ["full"], authors: [], missing: [], size: 1024, ...overrides };
  return { ...value, duplicateKey: duplicateAuditKey(value, match) };
}

test("strict matching remains the default and retains album, year and rounded duration", () => {
  const original = row();
  for (const change of [{ album: "Other release" }, { year: "2024" }, { duration: 190 }]) {
    assert.notEqual(original.duplicateKey, row(change).duplicateKey);
    assert.equal(duplicateGroupsFromAuditRows([original, row(change)]).length, 0);
  }
  assert.equal(original.duplicateKey, row({ duration: 181 }).duplicateKey);
  assert.equal(normalizeDuplicateMatch(undefined), "strict");
  assert.equal(normalizeDuplicateMatch("unknown"), "strict");
  assert.equal(duplicateAuditKey(original, "unknown"), original.duplicateKey);
});

test("explicit artist-title review groups versions across releases without changing the strict key", () => {
  const first = { artist: "Beyoncé & Jay-Z", title: "Déjà Vu (Remastered)", album: "Album A", year: 2006, duration: 240 };
  const second = { artist: "Beyonce and Jay Z", title: "Deja Vu", album: "Compilation", year: 2026, duration: 245 };
  assert.equal(duplicateAuditKey(first, "artist-title"), duplicateAuditKey(second, "artist-title"));
  assert.notEqual(duplicateAuditKey(first), duplicateAuditKey(second));
  assert.equal(duplicateGroupsFromAuditRows([row(first, "artist-title"), row(second, "artist-title")]).length, 1);
});

test("unknown or incomplete metadata is not a duplicate identity in either mode", () => {
  for (const mode of ["strict", "artist-title"]) {
    assert.equal(duplicateAuditKey({ artist: "Unknown Artist", title: "Song" }, mode), "");
    assert.equal(duplicateAuditKey({ artist: "Artist", title: "" }, mode), "");
    assert.equal(duplicateAuditKey(undefined, mode), "");
  }
});

test("meaningful live and acoustic qualifiers remain distinct in broad review", () => {
  assert.notEqual(normalizeDuplicateText("Song (Live)"), normalizeDuplicateText("Song (Acoustic)"));
  assert.notEqual(duplicateAuditKey(row({ title: "Song (Live)" }), "artist-title"), duplicateAuditKey(row({ title: "Song (Acoustic)" }), "artist-title"));
});

test("suggested keep ranks package completeness and retains per-file release details without mutating rows", () => {
  const sparse = row({ filePath: "C:\\songs\\sparse.feedpak", missing: ["Cover image"] }, "artist-title");
  const complete = row({ filePath: "C:\\songs\\complete.feedpak", album: "Another album", year: "2024", duration: 182,
    arrangements: 3, stems: 6, authors: ["Charter"] }, "artist-title");
  const original = structuredClone([sparse, complete]);
  const groups = duplicateGroupsFromAuditRows([sparse, complete]);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].files[0].filePath, complete.filePath);
  assert.equal(groups[0].files[0].recommended, true);
  assert.equal(groups[0].files[1].recommended, false);
  assert.equal(groups[0].files[0].album, complete.album);
  assert.equal(groups[0].files[0].year, complete.year);
  assert.equal(groups[0].files[0].duration, complete.duration);
  assert.deepEqual([sparse, complete], original);
});

test("unreadable packages and unidentifiable rows are excluded", () => {
  assert.deepEqual(duplicateGroupsFromAuditRows([row(), row({ status: "error" }), row({ artist: "" })]), []);
});
