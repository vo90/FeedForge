"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const fsp = fs.promises;
const path = require("node:path");
const os = require("node:os");
const crypto = require("node:crypto");
const { EventEmitter } = require("node:events");
const { setTimeout: delay } = require("node:timers/promises");
const { SongJobs } = require("../electron/song-browser/jobs.cjs");

const CHART = { id: "42", title: "One", artist: "Metallica", creator: "A creator" };
const PSARC = Buffer.concat([Buffer.from("PSAR"), Buffer.alloc(100, 7)]);
const FEEDPAK = Buffer.from("PK mock FeedPak output checked by the injected validator");
const TERMINAL = new Set(["completed", "failed", "cancelled"]);

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

async function until(predicate, message = "condition was not reached") {
  const limit = Date.now() + 3000;
  while (!predicate()) {
    if (Date.now() > limit) throw new Error(message);
    await delay(5);
  }
}

function inspect(preview = {}) {
  return { code: 0, stderr: "", stdout: JSON.stringify({ ok: true, preview: {
    title: "One", artist: "Metallica", song_count: 1, is_multi_song: false,
    arrangements: [{ id: "lead", type: "lead" }], ...preview,
  } }) };
}

async function fixture(t, options = {}) {
  const directory = await fsp.mkdtemp(path.join(os.tmpdir(), "feedforge-song-jobs-"));
  const root = path.join(directory, "jobs");
  const outputDir = path.join(directory, "output");
  const calls = [], contexts = [], downloads = [], events = [];
  const normalDownload = async (chart, args) => {
    downloads.push(chart.id);
    await fsp.writeFile(args.destination, PSARC);
    args.onProgress(100);
    return args.destination;
  };
  const normalConverter = async (args) => {
    calls.push(args);
    if (args[0] === "--inspect-json") return inspect(options.preview);
    if (args[0] === "--validate-feedpak") return {
      code: 0, stderr: "", stdout: JSON.stringify({ ok: true, results: [{
        input_path: args[1], validation: { ok: true, errors: [], warnings: [] },
      }] }),
    };
    assert.equal(args[1], "-o");
    await fsp.writeFile(args[2], FEEDPAK);
    return { code: 0, stdout: "Wrote and validated FeedPak", stderr: "" };
  };
  const manager = new SongJobs({ root, outputDir,
    download: options.download ? (chart, args) => options.download(chart, args, normalDownload) : normalDownload,
    runConverter: (args, context) => {
      contexts.push(context);
      return options.runConverter ? options.runConverter(args, context, normalConverter) : normalConverter(args);
    },
    emit: (job) => { events.push(job); options.emit?.(job); },
  });
  const result = async (id) => {
    await until(() => TERMINAL.has(manager.snapshot().find((job) => job.id === id)?.state), `job ${id} did not finish`);
    await manager.jobs.find((job) => job.id === id).done;
    return manager.snapshot().find((job) => job.id === id);
  };
  t.after(async () => {
    await manager.dispose();
    // Only the fresh test-owned directory can be recursively cleaned.
    assert.equal(path.dirname(directory), path.resolve(os.tmpdir()));
    assert.match(path.basename(directory), /^feedforge-song-jobs-/);
    await fsp.rm(directory, { recursive: true, force: true });
  });
  return { manager, root, outputDir, directory, calls, contexts, downloads, events, result };
}

test("converts, validates, saves without stems and persists only bounded public metadata", async (t) => {
  const f = await fixture(t);
  const job = f.manager.enqueue({ ...CHART, creator: "Someone https://host.test/token?secret=yes", url: "https://host.test/private-token", cookie: "secret-cookie" });
  const done = await f.result(job.id);
  assert.equal(done.state, "completed");
  assert.equal(done.progress, 100);
  assert.equal(done.outputPath, path.join(f.outputDir, "Metallica - One [CF 42].feedpak"));
  assert.deepEqual(await fsp.readFile(done.outputPath), FEEDPAK);
  assert.equal(done.sourceHash, crypto.createHash("sha256").update(PSARC).digest("hex"));
  assert.equal(done.outputHash, crypto.createHash("sha256").update(FEEDPAK).digest("hex"));
  const input = path.join(f.root, job.id, "source.psarc");
  const stage = path.join(f.root, job.id, "converted.feedpak");
  assert.deepEqual(f.calls, [["--inspect-json", input], [input, "-o", stage], ["--validate-feedpak", stage]]);
  assert.ok(f.contexts.every((context) => context.directory === path.join(f.root, job.id)));
  assert.deepEqual([...new Set(f.events.map((row) => row.state))], ["queued", "downloading", "inspecting", "converting", "validating", "completed"]);
  const ledger = await fsp.readFile(path.join(f.root, "jobs.json"), "utf8");
  assert.doesNotMatch(ledger, /https:|private-token|secret-cookie|secret=yes/);
  assert.equal(JSON.parse(ledger).jobs[0].creator, "Someone [link]");
  assert.deepEqual(await fsp.readdir(f.root), ["jobs.json"]);
  assert.deepEqual(await fsp.readdir(f.outputDir), [path.basename(done.outputPath)]);
});

test("rejects invalid record IDs and returns the same active job for repeat clicks", async (t) => {
  const f = await fixture(t);
  for (const id of ["../escape", "0", "1.5", "https://host.test/42", 1.5, "1234567890123"]) {
    assert.throws(() => f.manager.enqueue({ ...CHART, id }), /valid CustomsForge/);
  }
  const first = f.manager.enqueue(CHART);
  assert.equal(f.manager.enqueue({ ...CHART, title: "Another title" }).id, first.id);
  assert.equal(f.manager.snapshot().length, 1);
  assert.throws(() => f.manager.setOutputDir(path.join(f.directory, "other")), /Finish or cancel/);
  await f.result(first.id);
  assert.equal(f.downloads.length, 1);
  assert.equal(f.manager.setOutputDir(path.join(f.directory, "other")), path.join(f.directory, "other"));
});

test("rejects an HTML download before invoking the converter", async (t) => {
  const f = await fixture(t, { download: async (_chart, args) => {
    await fsp.writeFile(args.destination, "<!doctype html><html>Please log in before downloading this file.</html>");
    return args.destination;
  } });
  const done = await f.result(f.manager.enqueue(CHART).id);
  assert.equal(done.state, "failed");
  assert.match(done.error, /page or another file/);
  assert.equal(f.calls.length, 0);
  assert.equal(fs.existsSync(f.outputDir), false);
});

test("rejects a downloader returning a path outside its job directory", async (t) => {
  const f = await fixture(t, { download: async (_chart, args) => path.join(args.directory, "..", "other.psarc") });
  const done = await f.result(f.manager.enqueue(CHART).id);
  assert.equal(done.state, "failed");
  assert.match(done.error, /outside its job folder/);
  assert.equal(f.calls.length, 0);
});

test("rejects multi-song and mismatched song identities before conversion", async (t) => {
  for (const [preview, error] of [
    [{ song_count: 2, is_multi_song: true }, /multiple songs/],
    [{ title: "Unrelated advertisement" }, /Downloaded title does not match/],
    [{ artist: "Someone else" }, /Downloaded artist does not match/],
    [{ arrangements: [] }, /no readable playable arrangements/],
  ]) {
    await t.test(JSON.stringify(preview), async (st) => {
      const f = await fixture(st, { preview });
      const done = await f.result(f.manager.enqueue(CHART).id);
      assert.equal(done.state, "failed");
      assert.match(done.error, error);
      assert.equal(f.calls.length, 1);
    });
  }
});

test("identity check tolerates case, accents, punctuation and whitespace", async (t) => {
  const f = await fixture(t, { preview: { title: "  ONÉ!  ", artist: "METALLICA." } });
  assert.equal((await f.result(f.manager.enqueue(CHART).id)).state, "completed");
});

test("does not publish output when independent validation fails", async (t) => {
  const f = await fixture(t, { runConverter: async (args, _context, normal) => {
    if (args[0] === "--validate-feedpak") return { code: 0, stdout: JSON.stringify({ ok: false, results: [] }), stderr: "" };
    return normal(args);
  } });
  const done = await f.result(f.manager.enqueue(CHART).id);
  assert.equal(done.state, "failed");
  assert.match(done.error, /independent validation/);
  assert.equal(fs.existsSync(f.outputDir), false);
});

test("sanitizes and bounds converter errors without persisting host links", async (t) => {
  const f = await fixture(t, { runConverter: async () => ({ code: 1, stdout: "", stderr: "https://host.test/file?auth=secret " + "x".repeat(5000) }) });
  const done = await f.result(f.manager.enqueue(CHART).id);
  assert.equal(done.state, "failed");
  assert.ok(done.error.length <= 500);
  assert.doesNotMatch(done.error, /host\.test|secret/);
  assert.doesNotMatch(await fsp.readFile(path.join(f.root, "jobs.json"), "utf8"), /host\.test|secret/);
});

test("reuses an unchanged completed source hash and reconverts after its output changes", async (t) => {
  const f = await fixture(t);
  const first = await f.result(f.manager.enqueue(CHART).id);
  const duplicate = await f.result(f.manager.enqueue({ ...CHART, id: "43" }).id);
  assert.equal(duplicate.state, "completed");
  assert.equal(duplicate.duplicateOf, first.id);
  assert.equal(duplicate.outputPath, first.outputPath);
  assert.equal(f.calls.filter((args) => args[1] === "-o").length, 1);
  await fsp.writeFile(first.outputPath, "changed externally");
  const changed = await f.result(f.manager.enqueue(CHART).id);
  assert.equal(changed.state, "completed");
  assert.notEqual(changed.outputPath, first.outputPath);
  assert.equal(path.basename(changed.outputPath), "Metallica - One [CF 42] (2).feedpak");
  assert.equal(await fsp.readFile(first.outputPath, "utf8"), "changed externally");
  assert.equal(f.calls.filter((args) => args[1] === "-o").length, 2);
});

test("exclusive publication preserves an existing destination and selects a suffix", async (t) => {
  const f = await fixture(t);
  await fsp.mkdir(f.outputDir);
  const existing = path.join(f.outputDir, "Metallica - One [CF 42].feedpak");
  await fsp.writeFile(existing, "previous song");
  const done = await f.result(f.manager.enqueue(CHART).id);
  assert.equal(done.state, "completed");
  assert.equal(path.basename(done.outputPath), "Metallica - One [CF 42] (2).feedpak");
  assert.equal(await fsp.readFile(existing, "utf8"), "previous song");
  assert.equal((await fsp.readdir(f.outputDir)).some((name) => name.endsWith(".part")), false);
});

test("an unusable output folder fails without overwriting its existing file", async (t) => {
  const f = await fixture(t);
  await fsp.writeFile(f.outputDir, "not a directory");
  const done = await f.result(f.manager.enqueue(CHART).id);
  assert.equal(done.state, "failed");
  assert.equal(await fsp.readFile(f.outputDir, "utf8"), "not a directory");
  assert.deepEqual(await fsp.readdir(f.root), ["jobs.json"]);
});

test("queued cancellation skips download; active cancellation waits for downloader settlement", async (t) => {
  const entered = deferred(), release = deferred();
  let signal, secondStarted = false;
  const f = await fixture(t, { download: async (chart, args, normal) => {
    if (chart.id === "42") { signal = args.signal; entered.resolve(); await release.promise; }
    else secondStarted = true;
    return normal(chart, args);
  } });
  const first = f.manager.enqueue(CHART);
  const skipped = f.manager.enqueue({ ...CHART, id: "43" });
  const next = f.manager.enqueue({ ...CHART, id: "44" });
  await entered.promise;
  assert.equal((await f.manager.cancel(skipped.id)).state, "cancelled");
  let cancelled = false;
  const cancellation = f.manager.cancel(first.id).then((value) => { cancelled = true; return value; });
  await delay(25);
  assert.equal(signal.aborted, true);
  assert.equal(secondStarted, false);
  assert.equal(cancelled, false);
  release.resolve();
  assert.equal((await cancellation).state, "cancelled");
  assert.equal((await f.result(next.id)).state, "completed");
  assert.deepEqual(f.downloads, ["42", "44"]);
});

test("converter cancellation waits for the spawned process to close before the next job", async (t) => {
  const spawned = deferred(), finishRunner = deferred();
  let child, killed = 0;
  const f = await fixture(t, { runConverter: async (args, context, normal) => {
    if (!child) {
      child = new EventEmitter();
      child.exitCode = null;
      child.kill = () => { killed++; };
      context.onSpawn(child);
      spawned.resolve();
      await finishRunner.promise;
      return inspect();
    }
    return normal(args);
  } });
  const first = f.manager.enqueue(CHART);
  const next = f.manager.enqueue({ ...CHART, id: "43" });
  await spawned.promise;
  const cancellation = f.manager.cancel(first.id);
  finishRunner.resolve();
  await delay(25);
  assert.equal(killed, 1);
  assert.deepEqual(f.downloads, ["42"]);
  assert.equal(fs.existsSync(path.join(f.root, first.id)), true);
  child.exitCode = 1;
  child.emit("close", 1);
  assert.equal((await cancellation).state, "cancelled");
  assert.equal((await f.result(next.id)).state, "completed");
  assert.deepEqual(f.downloads, ["42", "43"]);
});

test("a runner rejection terminates and waits for its child before cleaning or advancing", async (t) => {
  const spawned = deferred();
  let child, killed = 0;
  const f = await fixture(t, { runConverter: async (args, context, normal) => {
    if (!child) {
      child = new EventEmitter();
      child.exitCode = null;
      child.kill = () => { killed++; };
      context.onSpawn(child);
      spawned.resolve();
      throw new Error("runner pipe failed");
    }
    return normal(args);
  } });
  const first = f.manager.enqueue(CHART);
  const next = f.manager.enqueue({ ...CHART, id: "43" });
  await spawned.promise;
  await until(() => killed === 1);
  assert.deepEqual(f.downloads, ["42"]);
  assert.equal(fs.existsSync(path.join(f.root, first.id)), true);
  child.exitCode = 1;
  child.emit("close", 1);
  const failed = await f.result(first.id);
  assert.equal(failed.state, "failed");
  assert.match(failed.error, /runner pipe failed/);
  assert.equal((await f.result(next.id)).state, "completed");
});

test("download progress and attention are bounded and observer errors cannot break jobs", async (t) => {
  const f = await fixture(t, { download: async (chart, args, normal) => {
    args.onProgress(-50);
    args.onAttention("Sign in at https://host.test/auth?token=secret");
    args.onProgress(200);
    args.onProgress(NaN);
    return normal(chart, args);
  }, emit: () => { throw new Error("UI disconnected"); } });
  const done = await f.result(f.manager.enqueue(CHART).id);
  assert.equal(done.state, "completed");
  assert.ok(f.events.every((event) => event.progress >= 0 && event.progress <= 100));
  assert.ok(f.events.some((event) => event.state === "needs_attention" && event.message === "Sign in at [link]"));
  assert.ok(f.events.some((event) => event.state === "downloading" && event.progress === 100));
});

test("recovery marks interrupted ledger rows failed without restarting or removing cached files", async (t) => {
  const f = await fixture(t);
  await f.manager.dispose();
  const id = crypto.randomUUID();
  const cached = path.join(f.root, id);
  await fsp.mkdir(cached);
  await fsp.writeFile(path.join(cached, "source.psarc"), PSARC);
  await fsp.writeFile(path.join(f.root, "jobs.json"), JSON.stringify({ version: 1, jobs: [{
    id, chartId: "42", title: "One", artist: "Metallica", creator: "Creator",
    state: "converting", progress: 50, message: "https://host.test/private", createdAt: 123,
  }] }));
  let downloads = 0;
  const recovered = new SongJobs({ root: f.root, outputDir: f.outputDir,
    download: async () => { downloads++; }, runConverter: async () => {},
  });
  t.after(() => recovered.dispose());
  assert.equal(recovered.snapshot()[0].state, "failed");
  assert.match(recovered.snapshot()[0].error, /interrupted/);
  await delay(15);
  assert.equal(downloads, 0);
  assert.deepEqual(await fsp.readFile(path.join(cached, "source.psarc")), PSARC);
  assert.doesNotMatch(await fsp.readFile(path.join(f.root, "jobs.json"), "utf8"), /https:/);
});

test("dispose drains an aborted download and cancels queued jobs", async (t) => {
  const entered = deferred();
  let receivedSignal;
  const f = await fixture(t, { download: async (_chart, args) => {
    receivedSignal = args.signal;
    entered.resolve();
    return new Promise((_resolve, reject) => args.signal.addEventListener("abort", () => reject(new Error("aborted")), { once: true }));
  } });
  f.manager.enqueue(CHART);
  f.manager.enqueue({ ...CHART, id: "43" });
  await entered.promise;
  await f.manager.dispose();
  assert.equal(receivedSignal.aborted, true);
  assert.ok(f.manager.snapshot().every((job) => job.state === "cancelled"));
  assert.equal(f.calls.length, 0);
  assert.throws(() => f.manager.enqueue(CHART), /closed/);
});
