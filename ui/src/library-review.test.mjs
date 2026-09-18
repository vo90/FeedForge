import test from "node:test";
import assert from "node:assert/strict";
import { filterLibraryRows, filterLibraryDuplicateGroups, libraryPage } from "./library-review.mjs";

const rows = [
  { filePath: "one", relativePath: "original.feedpak", artist: "Beyoncé", title: "Déjà Vu", album: "Original", status: "needs-work" },
  { filePath: "two", relativePath: "deluxe.feedpak", artist: "Beyoncé", title: "Déjà Vu", album: "Deluxe", status: "needs-work" },
  { filePath: "three", relativePath: "healthy.feedpak", artist: "Ghost", title: "Rats", album: "Prequelle", status: "pass" },
  { filePath: "four", relativePath: "broken.feedpak", status: "error" }
];
const duplicates = [{ artist: "Beyoncé", title: "Déjà Vu", files: rows.slice(0, 2) }];
const report = { rows, duplicates };

test("All includes healthy packages and filters distinguish duplicates from every issue", () => {
  assert.equal(filterLibraryRows(report).length, 4);
  assert.deepEqual(filterLibraryRows(report, { filter: "duplicates" }).map(row => row.filePath).sort(), ["one", "two"]);
  assert.deepEqual(filterLibraryRows(report, { filter: "issues" }).map(row => row.filePath).sort(), ["four", "one", "two"]);
});

test("search matches artist, title, album or filename with accents and multiple words", () => {
  for (const query of ["beyonce deja", "Beyoncé", "déjà"]) assert.equal(filterLibraryRows(report, { query }).length, 2);
  assert.equal(filterLibraryRows(report, { query: "prequelle rats" })[0].filePath, "three");
  assert.equal(filterLibraryRows(report, { query: "broken.feedpak", filter: "issues" })[0].filePath, "four");
  assert.equal(filterLibraryRows(report, { query: "Ghost", filter: "duplicates" }).length, 0);
});

test("matching a release retains every group member for comparison", () => {
  assert.equal(filterLibraryDuplicateGroups(duplicates, "deluxe").length, 1);
  assert.equal(filterLibraryDuplicateGroups(duplicates, "deluxe")[0].files.length, 2);
  assert.equal(filterLibraryDuplicateGroups(duplicates, "unmatched").length, 0);
});

test("browsing does not mutate reports and missing reports are safe", () => {
  const before = structuredClone(report);
  filterLibraryRows(report, { filter: "issues", query: "beyonce" });
  filterLibraryDuplicateGroups(duplicates, "deluxe");
  assert.deepEqual(report, before);
  assert.deepEqual(filterLibraryRows(undefined), []);
  assert.deepEqual(filterLibraryDuplicateGroups(undefined), []);
});

test("pagination exposes the whole library and clamps pages after a result change", () => {
  const many = Array.from({ length: 121 }, (_, index) => ({ filePath: String(index) }));
  assert.equal(libraryPage(many).rows.length, 50);
  assert.equal(libraryPage(many, 1).rows[0].filePath, "50");
  const last = libraryPage(many, 2);
  assert.equal(last.rows.length, 21);
  assert.equal(last.rows.at(-1).filePath, "120");
  assert.equal(last.pages, 3);
  assert.equal(libraryPage(many, 100).page, 2);
  assert.deepEqual(libraryPage([], 100), { rows: [], page: 0, pages: 1 });
});
