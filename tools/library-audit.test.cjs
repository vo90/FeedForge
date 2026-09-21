const test = require("node:test");
const assert = require("node:assert/strict");

const {
  duplicateAuditKey,
  duplicateGroupsFromAuditRows,
  normalizeDuplicateText
} = require("../electron/library-audit.cjs");

function row(overrides = {}) {
  return {
    filePath: "C:\\songs\\song.feedpak",
    relativePath: "song.feedpak",
    status: "pass",
    duplicateKey: "artist|song",
    title: "Song",
    artist: "Artist",
    album: "Album",
    year: "2020",
    duration: 180,
    arrangements: 1,
    stems: 1,
    stemIds: ["full"],
    authors: [],
    missing: [],
    size: 1024,
    ...overrides
  };
}

test("duplicate identity ignores album, year, duration, accents, and common edition labels", () => {
  const first = duplicateAuditKey({ artist: "Beyoncé & Jay-Z", title: "Déjà Vu (Remastered)", album: "Album A", year: 2006, duration: 240 });
  const second = duplicateAuditKey({ artist: "Beyonce and Jay Z", title: "Deja Vu", album: "Compilation", year: 2026, duration: 245 });
  assert.equal(first, second);
});

test("unknown or incomplete metadata is not treated as a duplicate identity", () => {
  assert.equal(duplicateAuditKey({ artist: "Unknown Artist", title: "Song" }), "");
  assert.equal(duplicateAuditKey({ artist: "Artist", title: "" }), "");
});

test("meaningful title qualifiers remain distinct", () => {
  assert.notEqual(normalizeDuplicateText("Song (Live)"), normalizeDuplicateText("Song (Acoustic)"));
});

test("duplicate groups recommend the most complete package", () => {
  const sparse = row({
    filePath: "C:\\songs\\sparse.feedpak",
    relativePath: "sparse.feedpak",
    arrangements: 1,
    stems: 1,
    missing: ["Cover image"]
  });
  const complete = row({
    filePath: "C:\\songs\\complete.feedpak",
    relativePath: "complete.feedpak",
    album: "Another album",
    year: "2024",
    arrangements: 3,
    stems: 6,
    authors: ["Charter"]
  });

  const groups = duplicateGroupsFromAuditRows([sparse, complete]);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].files[0].filePath, complete.filePath);
  assert.equal(groups[0].files[0].recommended, true);
  assert.equal(groups[0].files[1].recommended, false);
});

test("unreadable packages are excluded from duplicate groups", () => {
  const groups = duplicateGroupsFromAuditRows([
    row(),
    row({ filePath: "C:\\songs\\broken.feedpak", status: "error" })
  ]);
  assert.deepEqual(groups, []);
});
