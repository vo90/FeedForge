"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const { setTimeout: delay } = require("node:timers/promises");
const { BatchCoordinator, planBatch, MAX_BATCH_CHARTS } = require("../electron/song-browser/batch.cjs");

function chart(id, extra = {}) { return { id: String(id), artist: "Meshuggah", title: `Song ${id}`, parts: "Lead, Rhythm, Bass",
  tuning: "Bb Standard", version: "1.1", creator: "Griorb", host: "mediafire", supported: true, downloads: 100, ...extra }; }
function deferred() { let resolve; const promise = new Promise((yes) => { resolve = yes; }); return { promise, resolve }; }
async function until(predicate) { const end = Date.now() + 4000; while (!predicate()) { if (Date.now() > end) throw new Error("Condition did not complete."); await delay(3); } }
function fixture(t, options = {}) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "feedforge-batch-"));
  const root = path.join(directory, "batch");
  const outputDir = path.join(directory, "output");
  fs.mkdirSync(outputDir);
  const calls = [];
  const coordinator = new BatchCoordinator({ root, execute: async (item, context) => {
    calls.push({ item, context });
    return options.execute ? options.execute(item, context) : { status: "completed" };
  }, findCompleted: options.findCompleted, onChange: options.onChange });
  t.after(async () => {
    await coordinator.dispose();
    assert.equal(path.dirname(directory), path.resolve(os.tmpdir()));
    assert.match(path.basename(directory), /^feedforge-batch-/);
    fs.rmSync(directory, { recursive: true, force: true });
  });
  return { directory, root, outputDir, coordinator, calls,
    prepare: (charts, extra = {}) => coordinator.prepare({ charts, outputDir, ...extra }) };
}

test("planner preserves complementary arrangements and title qualifiers", () => {
  const plan = planBatch([
    chart(61228, { title: "Beneath", parts: "Lead", downloads: 261 }),
    chart(60885, { title: " beneath ", parts: "Bass", downloads: 192 }),
    chart(2, { title: "Beneath (Live)" }),
  ], { requiredParts: ["lead", "bass"] });
  assert.equal(plan.groups.length, 2);
  assert.deepEqual(new Set(plan.groups[0].recommendedIds), new Set(["61228", "60885"]));
  assert.match(plan.groups[0].reasons.join(" "), /Multiple charts/);
  assert.equal(plan.unresolvedCount, 0);
});

test("required paths and tuning take precedence over popularity and creator", () => {
  const plan = planBatch([
    chart(95417, { title: "The Demon's Name Is Surveillance", tuning: "Eb Standard", parts: "Lead, Rhythm, Bass", downloads: 184 }),
    chart(79584, { title: "The Demon's Name Is Surveillance", tuning: "Eb Standard", parts: "Lead, Rhythm", downloads: 237, creator: "Preferred" }),
    chart(53409, { title: "The Demon's Name Is Surveillance", parts: "Rhythm, Bass", downloads: 543 }),
  ], { requiredParts: ["lead", "bass"], tuning: "Eb Standard", preferredCreators: ["Preferred"] });
  assert.deepEqual(plan.selectedIds, ["95417"]);
  assert.equal(plan.groups[0].options.find((item) => item.id === "53409").eligible, false);
});

test("ambiguous tuning and missing coverage are not silently selected", () => {
  const plan = planBatch([chart(1, { tunings: ["Bb Standard", "Eb Standard"] }), chart(2, { parts: "Lead" })],
    { requiredParts: ["lead", "bass"], tuning: "Bb Standard" });
  assert.equal(plan.selectedIds.length, 0);
  assert.equal(plan.unresolvedCount, 2);
  assert.equal(plan.groups[0].options[0].eligible, false);
});

test("per-arrangement tuning supplies coverage and metadata is immutable and bounded", () => {
  const source = chart(1, { arrangements: [{ type: "lead", tuning: "Eb Standard" }, { type: "bass", tuning: "Bb Standard" }],
    url: "https://secret.example/download", title: "Test https://host.test/secret", cookie: "private" });
  const plan = planBatch([source], { requiredParts: ["lead"], tuning: "Eb Standard" });
  assert.deepEqual(plan.selectedIds, ["1"]);
  source.title = "Changed";
  assert.equal(plan.charts[0].title, "Test [link]");
  assert.equal(JSON.stringify(plan).includes("secret"), false);
  assert.equal(JSON.stringify(plan).includes("cookie"), false);
  assert.throws(() => planBatch(Array.from({ length: MAX_BATCH_CHARTS + 1 }, (_, i) => chart(i + 1))), /at most/);
});

test("duplicate chart IDs collapse only if the snapshot metadata agrees", () => {
  assert.equal(planBatch([chart(1), chart(1)]).charts.length, 1);
  assert.throws(() => planBatch([chart(1), chart(1, { version: "2" })]), /changed/);
});

test("preview does not execute and incomplete snapshots cannot start", (t) => {
  const f = fixture(t);
  const batch = f.prepare([chart(1)], { complete: false });
  assert.equal(f.calls.length, 0);
  assert.equal(batch.state, "draft");
  assert.throws(() => f.coordinator.start(batch.id), /incomplete/);
});

test("selection overrides use trusted IDs and preserve required combined coverage", (t) => {
  const f = fixture(t);
  const batch = f.prepare([chart(1, { title: "Beneath", parts: "lead" }), chart(2, { title: "Beneath", parts: "bass" })],
    { preferences: { requiredParts: ["lead", "bass"] } });
  assert.throws(() => f.coordinator.choose(batch.id, { selectedIds: ["999"] }), /preferences/);
  assert.throws(() => f.coordinator.choose(batch.id, { selectedIds: ["1"] }), /every required/);
  assert.deepEqual(f.coordinator.choose(batch.id, { selectedIds: ["2", "1"] }).selectedIds, ["2", "1"]);
});

test("more than 500 main-owned records can be prepared without a renderer metadata dependency", (t) => {
  const f = fixture(t);
  const batch = f.prepare(Array.from({ length: 610 }, (_, i) => chart(i + 1)));
  assert.equal(batch.charts.length, 610);
  assert.equal(batch.selectedIds.length, 610);
  assert.equal(f.calls.length, 0);
  assert.equal(fs.readFileSync(path.join(f.root, "batches.json"), "utf8").includes("https:"), false);
});

test("serial execution exceeds active queue and history limits without losing records", async (t) => {
  let active = 0, peak = 0;
  const f = fixture(t, { execute: async () => { active++; peak = Math.max(peak, active); await Promise.resolve(); active--; return { status: "completed" }; } });
  const batch = f.prepare(Array.from({ length: 105 }, (_, i) => chart(i + 1)));
  f.coordinator.start(batch.id);
  await f.coordinator.waitForIdle();
  assert.equal(f.calls.length, 105);
  assert.equal(peak, 1);
  assert.equal(f.coordinator.get(batch.id).counts.completed, 105);
  assert.equal(f.coordinator.get(batch.id).state, "completed");
});

test("pause finishes the active song, preserves output location and resumes only remaining songs", async (t) => {
  const gate = deferred();
  const f = fixture(t, { execute: async (_chart, context) => { await gate.promise; assert.equal(context.signal.aborted, false); return { status: "completed" }; } });
  const batch = f.prepare([chart(1), chart(2)]);
  f.coordinator.start(batch.id);
  await until(() => f.calls.length === 1);
  f.coordinator.pause(batch.id);
  gate.resolve();
  await f.coordinator.waitForIdle();
  assert.equal(f.calls.length, 1);
  assert.equal(f.coordinator.get(batch.id).state, "paused");
  f.coordinator.resume(batch.id);
  await f.coordinator.waitForIdle();
  assert.deepEqual(f.calls.map(({ context }) => context.outputDir), [f.outputDir, f.outputDir]);
  assert.equal(f.coordinator.get(batch.id).counts.completed, 2);
});

test("host attention parks one song while later songs complete", async (t) => {
  const f = fixture(t, { execute: async (item) => item.id === "1" ? { status: "needs_attention", message: "Choose a file." } : { status: "completed" } });
  const batch = f.prepare([chart(1), chart(2)]);
  f.coordinator.start(batch.id);
  await f.coordinator.waitForIdle();
  const done = f.coordinator.get(batch.id);
  assert.equal(f.calls.length, 2);
  assert.equal(done.counts.needs_attention, 1);
  assert.equal(done.counts.completed, 1);
  assert.equal(done.state, "paused");
});

test("session-wide CustomsForge attention pauses every batch without dispatching other songs", async (t) => {
  const f = fixture(t, { execute: async () => ({ status: "needs_attention", sessionWide: true, message: "Sign in." }) });
  const first = f.prepare([chart(1), chart(2)]);
  const second = f.prepare([chart(3)]);
  f.coordinator.start(first.id); f.coordinator.start(second.id);
  await f.coordinator.waitForIdle();
  assert.equal(f.calls.length, 1);
  assert.ok(f.coordinator.snapshot().every((batch) => batch.state === "paused"));
});

test("cancel waits for active cleanup while preserving a concurrently published result", async (t) => {
  let cleaned = false;
  const f = fixture(t, { execute: async (_chart, context) => {
    await new Promise((resolve) => context.signal.addEventListener("abort", resolve, { once: true }));
    await delay(5); cleaned = true;
    return { status: "completed", outputPath: path.join(f.outputDir, "done.feedpak") };
  } });
  const batch = f.prepare([chart(1), chart(2)]);
  f.coordinator.start(batch.id); await until(() => f.calls.length === 1);
  await f.coordinator.cancel(batch.id);
  assert.equal(cleaned, true);
  assert.equal(f.coordinator.get(batch.id).counts.completed, 1);
  assert.equal(f.coordinator.get(batch.id).counts.cancelled, 1);
});

test("shutdown interrupts active work and restart requires explicit resume with receipt recovery", async (t) => {
  const f = fixture(t, { execute: async (_chart, context) => {
    await new Promise((resolve) => context.signal.addEventListener("abort", resolve, { once: true }));
    return { status: "cancelled" };
  } });
  const batch = f.prepare([chart(1), chart(2)]);
  f.coordinator.start(batch.id); await until(() => f.calls.length === 1);
  await f.coordinator.dispose();
  const calls = [];
  const restarted = new BatchCoordinator({ root: f.root, execute: async (item) => { calls.push(item.id); return { status: "completed" }; },
    findCompleted: async ({ chart: item }) => item.id === "1" ? { status: "completed" } : null });
  assert.equal(restarted.get(batch.id).state, "paused");
  assert.equal(restarted.get(batch.id).counts.interrupted, 1);
  await delay(5); assert.equal(calls.length, 0);
  restarted.resume(batch.id); await restarted.waitForIdle();
  assert.deepEqual(calls, ["2"]);
  assert.equal(restarted.get(batch.id).counts.completed, 2);
  await restarted.dispose();
});

test("saved file choices reject injected options and survive resume and restart", async (t) => {
  let attempts = 0;
  const f = fixture(t, { execute: async (_chart, context) => {
    attempts++;
    if (attempts === 1) return { status: "needs_attention", jobId: "previous", candidates: [
      { id: "opaque-file-1", label: "Beneath_p.psarc", platform: "pc", url: "https://secret" },
      { id: "opaque-file-2", label: "Beneath_m.psarc", platform: "mac" },
    ] };
    assert.deepEqual(context.selection.choice, { id: "opaque-file-1", label: "Beneath_p.psarc", platform: "pc" });
    return { status: "completed" };
  } });
  const batch = f.prepare([chart(1)]);
  f.coordinator.start(batch.id); await f.coordinator.waitForIdle();
  const item = f.coordinator.get(batch.id).items[0];
  assert.equal(item.outcome.jobId, "previous");
  assert.throws(() => f.coordinator.setItemChoice(batch.id, item.id, { choice: { label: "evil.psarc", platform: "pc" } }), /saved options/);
  f.coordinator.setItemChoice(batch.id, item.id, { choice: { id: "opaque-file-1" } });
  f.coordinator.resume(batch.id); await f.coordinator.waitForIdle();
  assert.equal(f.coordinator.get(batch.id).counts.completed, 1);
  const restarted = new BatchCoordinator({ root: f.root, execute: async () => { throw new Error("Unexpected execution."); } });
  assert.equal(restarted.get(batch.id).items[0].choice.label, "Beneath_p.psarc");
  assert.equal(fs.readFileSync(path.join(f.root, "batches.json"), "utf8").includes("https:"), false);
  await restarted.dispose();
});

test("failed items require an explicit retry and successful items are preserved", async (t) => {
  let failed = false;
  const f = fixture(t, { execute: async (item) => {
    if (item.id === "1" && !failed) { failed = true; throw new Error("Unavailable https://host.test/private"); }
    return { status: "completed" };
  } });
  const batch = f.prepare([chart(1), chart(2)]);
  f.coordinator.start(batch.id); await f.coordinator.waitForIdle();
  assert.equal(f.coordinator.get(batch.id).counts.failed, 1);
  f.coordinator.resume(batch.id, { retryFailed: true }); await f.coordinator.waitForIdle();
  assert.deepEqual(f.calls.map(({ item }) => item.id), ["1", "2", "1"]);
  assert.equal(f.coordinator.get(batch.id).counts.completed, 2);
});

test("corrupt or future-version batch ledgers are preserved and rejected", (t) => {
  const f = fixture(t);
  f.prepare([chart(1)]);
  const filename = path.join(f.root, "batches.json");
  const original = fs.readFileSync(filename, "utf8");
  fs.writeFileSync(filename, JSON.stringify({ version: 999, batches: [] }));
  assert.throws(() => new BatchCoordinator({ root: f.root, execute: async () => {} }), /unsupported/);
  assert.equal(JSON.parse(fs.readFileSync(filename, "utf8")).version, 999);
  fs.writeFileSync(filename, original);
});

test("a failed progress write stops later dispatch but keeps a published song successful", async (t) => {
  let restore;
  const f = fixture(t, { execute: async () => {
    const original = fs.renameSync;
    fs.renameSync = (source, destination) => {
      if (destination === path.join(f.root, "batches.json")) throw new Error("Disk unavailable.");
      return original(source, destination);
    };
    restore = () => { fs.renameSync = original; };
    return { status: "completed", outputPath: path.join(f.outputDir, "done.feedpak") };
  } });
  const batch = f.prepare([chart(1), chart(2)]);
  try {
    f.coordinator.start(batch.id); await f.coordinator.waitForIdle();
    assert.equal(f.calls.length, 1);
    assert.equal(f.coordinator.get(batch.id).counts.completed, 1);
    assert.equal(f.coordinator.get(batch.id).state, "paused");
    assert.match(f.coordinator.get(batch.id).warning, /No more songs/);
  } finally { restore?.(); }
});

test("unknown chart flags remain unknown and unspecified backing-track variants stay permissive", () => {
  const plan = planBatch([chart(1)]);
  assert.equal(plan.charts[0].reported, null);
  assert.equal(plan.charts[0].abandoned, null);
  assert.equal(plan.preferences.backingTrack, "any");
});

test("interactive resolution retries only its selected item and leaves other pending songs paused", async (t) => {
  const gate = deferred();
  const f = fixture(t, { execute: async (item, context) => {
    if (context.interactive) { await gate.promise; return { status: "completed" }; }
    if (item.id === "1") return { status: "needs_attention", sessionWide: true, message: "Sign in." };
    return { status: "completed" };
  } });
  const batch = f.prepare([chart(1), chart(2), chart(3)]);
  f.coordinator.start(batch.id); await f.coordinator.waitForIdle();
  const waiting = f.coordinator.get(batch.id).items[0];
  f.coordinator.resumeItem(batch.id, waiting.id);
  await until(() => f.calls.length === 2);
  assert.equal(f.calls[1].context.interactive, true);
  assert.equal(f.coordinator.get(batch.id).state, "paused");
  assert.throws(() => f.coordinator.resume(batch.id), /active song resolution/);
  gate.resolve(); await f.coordinator.waitForIdle();
  assert.equal(f.calls.length, 2);
  assert.equal(f.coordinator.get(batch.id).counts.completed, 1);
  assert.equal(f.coordinator.get(batch.id).counts.pending, 2);
  assert.equal(f.coordinator.get(batch.id).state, "paused");
  f.coordinator.resume(batch.id); await f.coordinator.waitForIdle();
  assert.deepEqual(f.calls.map(({ item, context }) => [item.id, context.interactive]), [["1", false], ["1", true], ["2", false], ["3", false]]);
});

test("interactive resolution is explicit, supports failed finished batches and is not restored after restart", async (t) => {
  const f = fixture(t, { execute: async () => ({ status: "failed", message: "Host unavailable." }) });
  const batch = f.prepare([chart(1)]);
  f.coordinator.start(batch.id); await f.coordinator.waitForIdle();
  const item = f.coordinator.get(batch.id).items[0];
  assert.equal(f.coordinator.get(batch.id).state, "completed");
  assert.throws(() => f.coordinator.resumeItem(batch.id, "untrusted"), /Choose a song/);
  f.coordinator.resumeItem(batch.id, item.id);
  // Capture the saved authorization before its scheduled microtask starts. A newly opened app still waits for explicit input.
  const calls = [];
  const restarted = new BatchCoordinator({ root: f.root, execute: async (_chart, context) => { calls.push(context); return { status: "completed" }; } });
  await f.coordinator.waitForIdle();
  await delay(5);
  assert.equal(calls.length, 0);
  assert.equal(restarted.get(batch.id).state, "paused");
  assert.equal(restarted.get(batch.id).interactiveItemId, undefined);
  restarted.resume(batch.id); await restarted.waitForIdle();
  assert.equal(calls[0].interactive, false);
  await restarted.dispose();
});
