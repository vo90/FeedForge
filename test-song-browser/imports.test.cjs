"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const crypto = require("node:crypto");
const { ImportIndex } = require("../electron/song-browser/imports.cjs");

const CHART = { id: "61228", title: "Beneath", artist: "Meshuggah", creator: "Griorb", version: "1.1",
  updated: "2026-09-01", parts: "lead", tuning: "Bb Standard", supported: true, host: "mediafire" };
const CONTENT = Buffer.from("PK validated FeedPak supplied by the trusted completion path");
const HASH = crypto.createHash("sha256").update(CONTENT).digest("hex");

function fixture(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "feedforge-imports-"));
  const root = path.join(directory, "index");
  const outputPath = path.join(directory, "Beneath.feedpak");
  fs.writeFileSync(outputPath, CONTENT);
  const index = new ImportIndex({ root });
  const entry = (extra = {}) => ({ ...CHART, chartId: CHART.id, id: crypto.randomUUID(), state: "completed", sourceHash: "a".repeat(64),
    outputHash: HASH, outputPath, coverage: { arrangements: [{ type: "lead", tuning: "Bb Standard" }] },
    selection: { requiredParts: ["lead"], tuning: "Bb Standard" }, ...extra });
  t.after(() => {
    assert.equal(path.dirname(directory), path.resolve(os.tmpdir()));
    assert.match(path.basename(directory), /^feedforge-imports-/);
    fs.rmSync(directory, { recursive: true, force: true });
  });
  return { index, entry, directory, root, outputPath };
}

test("completed records persist independently of the bounded job history", async (t) => {
  const f = fixture(t);
  for (let i = 0; i < 120; i++) f.index.record(f.entry());
  const restored = new ImportIndex({ root: f.root });
  assert.equal(restored.snapshot().length, 120);
  assert.ok(await restored.find(CHART, { preferences: { requiredParts: ["lead"], tuning: "Bb Standard" } }));
});

test("attempt recovery verifies the output before returning a completed record", async (t) => {
  const f = fixture(t);
  const batchId = crypto.randomUUID(), itemId = `${batchId}:${CHART.id}`;
  const record = f.index.record(f.entry({ batchId, itemId }));
  assert.equal((await f.index.findByAttempt(batchId, itemId)).id, record.id);
  fs.writeFileSync(f.outputPath, "changed");
  assert.equal(await f.index.findByAttempt(batchId, itemId), null);
  assert.equal(await f.index.verify(record), false);
  fs.unlinkSync(f.outputPath);
  assert.equal(await f.index.findByAttempt(batchId, itemId), null);
});

test("same artist and title do not skip another creator's chart or a new revision", async (t) => {
  const f = fixture(t); f.index.record(f.entry());
  assert.equal(await f.index.find({ ...CHART, id: "60885" }), null);
  assert.equal(await f.index.find({ ...CHART, version: "1.2" }), null);
  assert.equal(await f.index.find({ ...CHART, updated: "2026-09-02" }), null);
  assert.equal(await f.index.find({ ...CHART, version: "", updated: "" }), null);
  assert.ok(await f.index.find({ ...CHART, version: "", updated: "" }, { sourceHash: "a".repeat(64) }));
});

test("a known chart is not reused for missing arrangements, another tuning or backing track", async (t) => {
  const f = fixture(t); f.index.record(f.entry());
  assert.equal(await f.index.find(CHART, { preferences: { requiredParts: ["bass"] } }), null);
  assert.equal(await f.index.find(CHART, { preferences: { requiredParts: ["lead"], tuning: "Eb Standard" } }), null);
  assert.equal(await f.index.find(CHART, { preferences: { backingTrack: "no-guitar" } }), null);
  assert.equal(await f.index.find(CHART, { preferences: { platform: "mac" } }), null);
  assert.ok(await f.index.find(CHART, { preferences: { requiredParts: ["lead"], tuning: "Bb Standard" } }));
});

test("reuse in another output folder requires an explicit copy path", async (t) => {
  const f = fixture(t); f.index.record(f.entry());
  const other = path.join(f.directory, "other"); fs.mkdirSync(other);
  assert.equal(await f.index.find(CHART, { outputDir: other }), null);
  assert.ok(await f.index.find(CHART, { outputDir: f.directory }));
});

test("trusted completion metadata is sanitized and source objects cannot mutate persisted entries", (t) => {
  const f = fixture(t);
  const entry = f.entry({ creator: "Griorb https://private.test/signed?token=x", url: "https://private.test", cookie: "secret",
    selection: { requiredParts: ["lead"], url: "https://private.test" } });
  f.index.record(entry); entry.title = "Changed";
  const encoded = fs.readFileSync(path.join(f.root, "imports.json"), "utf8");
  assert.equal(encoded.includes("https:"), false);
  assert.equal(encoded.includes("cookie"), false);
  assert.equal(f.index.snapshot()[0].chart.title, "Beneath");
});

test("only completed entries with an absolute FeedPak path and hash are accepted", (t) => {
  const f = fixture(t);
  assert.throws(() => f.index.record(f.entry({ state: "failed" })), /successfully completed/);
  assert.throws(() => f.index.record(f.entry({ outputPath: "relative.feedpak" })), /FeedPak path/);
  assert.throws(() => f.index.record(f.entry({ outputHash: "bad" })), /SHA-256/);
  assert.equal(f.index.snapshot().length, 0);
});

test("future or corrupted import data is rejected without replacing its contents", (t) => {
  const f = fixture(t);
  const filename = path.join(f.root, "imports.json");
  fs.writeFileSync(filename, JSON.stringify({ version: 2, records: [] }));
  assert.throws(() => new ImportIndex({ root: f.root }), /unsupported/);
  assert.equal(JSON.parse(fs.readFileSync(filename, "utf8")).version, 2);
});

test("real converter guitar IDs and tuning offset arrays remain reusable after restart", async (t) => {
  const f = fixture(t);
  f.index.record(f.entry({ updated: undefined, chartUpdated: "2026-09-01", coverage: {
    arrangements: [{ id: "lead", type: "guitar", tuning: [-6, -6, -6, -6, -6, -6] }], source_platforms: ["pc"],
  } }));
  const restored = new ImportIndex({ root: f.root });
  assert.equal(restored.snapshot()[0].chart.updated, "2026-09-01");
  assert.deepEqual(restored.snapshot()[0].coverage.arrangements[0].tuning, [-6, -6, -6, -6, -6, -6]);
  assert.ok(await restored.find(CHART, { preferences: { requiredParts: ["lead"], tuning: "Bb Standard" } }));
  assert.equal(await restored.find(CHART, { preferences: { requiredParts: ["rhythm"], tuning: "Bb Standard" } }), null);
});

test("unchanged completion events do not rewrite the import ledger", (t) => {
  const f = fixture(t);
  const entry = f.entry(); f.index.record(entry);
  const filename = path.join(f.root, "imports.json");
  fs.utimesSync(filename, new Date("2020-01-01"), new Date("2020-01-01"));
  const before = fs.statSync(filename).mtimeMs;
  f.index.record(entry);
  assert.equal(fs.statSync(filename).mtimeMs, before);
});
