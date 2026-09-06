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
const RECIPE = { version: "0.1.40", build: "a".repeat(64), options: { audio: "vorbis" } };

function fixture(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "feedforge-imports-"));
  const root = path.join(directory, "index");
  const outputPath = path.join(directory, "Beneath.feedpak");
  fs.writeFileSync(outputPath, CONTENT);
  const index = new ImportIndex({ root });
  const entry = (extra = {}) => ({ ...CHART, chartId: CHART.id, id: crypto.randomUUID(), state: "completed", sourceHash: "a".repeat(64),
    outputHash: HASH, outputPath, coverage: { arrangements: [{ type: "lead", tuning: "Bb Standard" }] },
    selection: { requiredParts: ["lead"], tuning: "Bb Standard" }, recipe: RECIPE,
    resolvedFile: { filename: "Meshuggah_Beneath_p.psarc", platform: "pc", evidence: { filename: "observed", platform: "filename_hint" } }, ...extra });
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
  assert.ok(await restored.find(CHART, { recipe: RECIPE, preferences: { requiredParts: ["lead"], tuning: "Bb Standard" } }));
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
  assert.equal(await f.index.find({ ...CHART, id: "60885" }, { recipe: RECIPE }), null);
  assert.equal(await f.index.find({ ...CHART, version: "1.2" }, { recipe: RECIPE }), null);
  assert.equal(await f.index.find({ ...CHART, updated: "2026-09-02" }, { recipe: RECIPE }), null);
  assert.equal(await f.index.find({ ...CHART, version: "", updated: "" }, { recipe: RECIPE }), null);
  assert.ok(await f.index.find({ ...CHART, version: "", updated: "" }, { recipe: RECIPE, sourceHash: "a".repeat(64) }));
});

test("a known chart is not reused for missing arrangements, another tuning or backing track", async (t) => {
  const f = fixture(t); f.index.record(f.entry());
  assert.equal(await f.index.find(CHART, { recipe: RECIPE, preferences: { requiredParts: ["bass"] } }), null);
  assert.equal(await f.index.find(CHART, { recipe: RECIPE, preferences: { requiredParts: ["lead"], tuning: "Eb Standard" } }), null);
  assert.equal(await f.index.find(CHART, { recipe: RECIPE, preferences: { backingTrack: "no-guitar", backingStrict: true } }), null);
  assert.equal(await f.index.find(CHART, { recipe: RECIPE, preferences: { platform: "mac", strictPlatform: true } }), null);
  assert.ok(await f.index.find(CHART, { recipe: RECIPE, preferences: { requiredParts: ["lead"], tuning: "Bb Standard" } }));
});

test("reuse in another output folder requires an explicit copy path", async (t) => {
  const f = fixture(t); f.index.record(f.entry());
  const other = path.join(f.directory, "other"); fs.mkdirSync(other);
  assert.equal(await f.index.find(CHART, { recipe: RECIPE, outputDir: other }), null);
  assert.ok(await f.index.find(CHART, { recipe: RECIPE, outputDir: f.directory }));
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
  fs.writeFileSync(filename, JSON.stringify({ version: 3, records: [] }));
  assert.throws(() => new ImportIndex({ root: f.root }), /unsupported/);
  assert.equal(JSON.parse(fs.readFileSync(filename, "utf8")).version, 3);
});

test("real converter guitar IDs and tuning offset arrays remain reusable after restart", async (t) => {
  const f = fixture(t);
  f.index.record(f.entry({ updated: undefined, chartUpdated: "2026-09-01", coverage: {
    arrangements: [{ id: "lead", type: "guitar", tuning: [-6, -6, -6, -6, -6, -6] }], source_platforms: ["pc"],
  } }));
  const restored = new ImportIndex({ root: f.root });
  assert.equal(restored.snapshot()[0].chart.updated, "2026-09-01");
  assert.deepEqual(restored.snapshot()[0].coverage.arrangements[0].tuning, [-6, -6, -6, -6, -6, -6]);
  assert.ok(await restored.find(CHART, { recipe: RECIPE, preferences: { requiredParts: ["lead"], tuning: "Bb Standard" } }));
  assert.equal(await restored.find(CHART, { recipe: RECIPE, preferences: { requiredParts: ["rhythm"], tuning: "Bb Standard" } }), null);
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

test("v1 migration preserves an exact backup and marks legacy provenance unknown", async (t) => {
  const f = fixture(t);
  const entry = f.entry({ intentId: crypto.randomUUID(), requestedChoice: { label: "Legacy_p.psarc", platform: "pc" } });
  const filename = path.join(f.root, "imports.json");
  const original = JSON.stringify({ version: 1, records: [entry] }, null, 2);
  fs.writeFileSync(filename, original);
  const migrated = new ImportIndex({ root: f.root });
  assert.equal(fs.readFileSync(migrated.migrationBackup, "utf8"), original);
  assert.equal(JSON.parse(fs.readFileSync(filename)).version, 2);
  const record = migrated.snapshot()[0];
  for (const key of ["recipe", "intentId", "requestedChoice", "resolvedFile"]) assert.equal(record[key], null);
  assert.equal(await migrated.verify(record), true);
  assert.equal(await migrated.find(CHART, { recipe: RECIPE }), null);
  assert.equal((await migrated.assess(CHART, { recipe: RECIPE })).status, "insufficient_evidence");
  const backups = fs.readdirSync(f.root).filter((name) => name.includes("backup"));
  new ImportIndex({ root: f.root });
  assert.deepEqual(fs.readdirSync(f.root).filter((name) => name.includes("backup")), backups);
});

test("failed atomic migration leaves original and backup intact and can be retried", (t) => {
  const f = fixture(t), filename = path.join(f.root, "imports.json");
  const original = JSON.stringify({ version: 1, records: [f.entry()] });
  fs.writeFileSync(filename, original);
  const rename = fs.renameSync;
  fs.renameSync = (from, to) => { if (to === filename) throw new Error("Simulated failed durable write"); return rename(from, to); };
  try { assert.throws(() => new ImportIndex({ root: f.root }), /failed durable write/); }
  finally { fs.renameSync = rename; }
  assert.equal(fs.readFileSync(filename, "utf8"), original);
  assert.equal(fs.readFileSync(path.join(f.root, "imports.v1.backup.json"), "utf8"), original);
  assert.equal(new ImportIndex({ root: f.root }).snapshot().length, 1);
  assert.equal(JSON.parse(fs.readFileSync(filename)).version, 2);
});

test("explicit variant decisions survive restart and identical bytes permit reuse only after download", async (t) => {
  const f = fixture(t);
  f.index.record(f.entry({ requestedChoice: { label: "Meshuggah_Beneath_p.psarc", platform: "pc" } }));
  const restored = new ImportIndex({ root: f.root });
  assert.equal(restored.snapshot()[0].requestedChoice.label, "Meshuggah_Beneath_p.psarc");
  const request = { recipe: RECIPE, requestedChoice: { label: "Meshuggah_Beneath_v2_p.psarc", platform: "pc" } };
  assert.equal((await restored.assess(CHART, request)).status, "choose_file");
  assert.equal(await restored.find(CHART, request), null);
  assert.ok(await restored.find(CHART, { ...request, sourceHash: "a".repeat(64) }));
  assert.equal(await restored.find(CHART, { ...request, sourceHash: "b".repeat(64) }), null);
  assert.equal(await restored.find(CHART, { ...request, sourceHash: "a".repeat(64), recipe: { ...RECIPE, version: "2" } }), null);
});

test("intent recovery cannot recover a previous choice but preserves its published success after upgrade", async (t) => {
  const f = fixture(t), batchId = crypto.randomUUID(), itemId = `${batchId}:${CHART.id}`, intentId = crypto.randomUUID();
  const record = f.index.record(f.entry({ batchId, itemId, intentId }));
  const restored = new ImportIndex({ root: f.root });
  assert.equal((await restored.findByAttempt(batchId, itemId, { intentId })).id, record.id);
  assert.equal(await restored.findByAttempt(batchId, itemId, { intentId: crypto.randomUUID() }), null);
  assert.equal(await restored.findByAttempt(batchId, itemId), null);
  assert.equal(await restored.findByAttempt(batchId, itemId, { intentId, jobId: crypto.randomUUID() }), null);
  assert.equal(await restored.find(CHART, { recipe: { ...RECIPE, version: "2" } }), null);
  assert.equal((await restored.findByAttempt(batchId, itemId, { intentId, jobId: record.id })).id, record.id);
});

test("preview decisions distinguish availability, changed output and unknown provenance and can cancel", async (t) => {
  const f = fixture(t); f.index.record(f.entry());
  const options = { recipe: RECIPE, outputDir: f.directory };
  assert.equal((await f.index.assess(CHART, options)).status, "available");
  assert.equal((await f.index.assess(CHART, { ...options, reviewAnother: true })).code, "review_file");
  assert.equal((await f.index.assess(CHART)).code, "unknown_recipe");
  assert.equal((await f.index.assess({ ...CHART, id: "111" }, options)).status, "new_conversion");
  fs.writeFileSync(f.outputPath, "changed after preview");
  assert.equal((await f.index.assess(CHART, options)).code, "missing_output");
  assert.equal(await f.index.find(CHART, options), null);
  const controller = new AbortController(); controller.abort();
  await assert.rejects(f.index.assess(CHART, { ...options, signal: controller.signal }), /cancelled/);
  await assert.rejects(f.index.assess({ ...CHART, id: "111" }, { ...options, signal: controller.signal }), /cancelled/);
});

test("explicit instrument and backing evidence survives storage without promoting unknown fields", async (t) => {
  const f = fixture(t);
  f.index.record(f.entry({ coverage: { arrangements: [{ id: "lead", type: "guitar", instrument_family: "guitar",
    instrument_family_evidence: "explicit", string_count: 6, string_count_evidence: "explicit", minimum_used_strings: 4 }],
    source_platforms: ["pc"], backing_track: "full", backing_track_evidence: "explicit" },
    selection: { instrumentRequirements: [{ part: "lead", family: "guitar", stringCount: 6, strict: true }], backingTrack: "full", backingStrict: true } }));
  const restored = new ImportIndex({ root: f.root }), saved = restored.snapshot()[0];
  assert.equal(saved.coverage.arrangements[0].string_count_evidence, "explicit");
  assert.equal(saved.coverage.arrangements[0].minimum_used_strings, 4);
  assert.equal(saved.selection.instrumentRequirements[0].stringCount, 6);
  assert.ok(await restored.find(CHART, { recipe: RECIPE, preferences: saved.selection }));
  const incompatible = { ...saved.selection, instrumentRequirements: [{ part: "lead", family: "guitar", stringCount: 5, strict: true }] };
  assert.equal(await restored.find(CHART, { recipe: RECIPE, preferences: incompatible }), null);
});

test("replayed legacy history cannot erase known provenance for the same completed bytes", (t) => {
  const f = fixture(t), entry = f.entry({ intentId: crypto.randomUUID() });
  f.index.record(entry);
  f.index.record({ ...entry, recipe: undefined, resolvedFile: undefined, intentId: undefined });
  const saved = f.index.snapshot()[0];
  assert.equal(saved.intentId, entry.intentId);
  assert.equal(saved.resolvedFile.filename, entry.resolvedFile.filename);
  assert.equal(saved.recipe.version, RECIPE.version);
});

test("job-scoped assessment cannot borrow another completed job's compatible provenance", async (t) => {
  const f = fixture(t);
  const incompatible = f.index.record(f.entry({ recipe: { ...RECIPE, build: "b".repeat(64) }, completedAt: 10 }));
  const compatible = f.index.record(f.entry({ completedAt: 20 }));
  const options = { recipe: RECIPE, sourceHash: "a".repeat(64) };
  assert.equal((await f.index.assess(CHART, options)).record.id, compatible.id);
  const scoped = await f.index.assess(CHART, { ...options, jobId: incompatible.id });
  assert.equal(scoped.reusable, false);
  assert.equal(scoped.code, "different_recipe");
  assert.equal(scoped.record, undefined);
  assert.equal(await f.index.find(CHART, { ...options, jobId: incompatible.id }), null);
  assert.equal((await f.index.assess(CHART, { ...options, jobId: compatible.id })).record.id, compatible.id);
  assert.equal((await f.index.assess(CHART, { ...options, jobId: crypto.randomUUID() })).code, "no_record");
});
