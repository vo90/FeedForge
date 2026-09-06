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

const CHART = { id: "42", title: "One", artist: "Metallica", creator: "A creator", host: "mediafire", supported: true };
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
  assert.deepEqual(await fsp.readdir(f.root), ["cache", "jobs.json"]);
  assert.deepEqual(await fsp.readdir(path.join(f.root, "cache")), []);
  assert.equal(done.hasCachedInput, false);
  assert.deepEqual(await fsp.readdir(f.outputDir), [path.basename(done.outputPath)]);
});

test('MEGA pre-save progress and phase messages reach the existing conversion queue', async (t) => {
  const f = await fixture(t, { download: async (chart, args, normal) => {
    args.onProgress(47, { phase: 'downloading' });
    args.onProgress(94, { phase: 'decrypting' });
    args.onProgress(95, { phase: 'saving' });
    return normal(chart, args);
  } });
  const job = f.manager.enqueue({ ...CHART, host: 'mega' });
  assert.equal((await f.result(job.id)).state, 'completed');
  for (const message of ['Downloading from MEGA.', 'Decrypting the MEGA download.', 'Saving the downloaded PSARC.']) {
    assert.ok(f.events.some(event => event.state === 'downloading' && event.message === message), message);
  }
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
  assert.deepEqual(await fsp.readdir(f.outputDir), []);
  assert.equal(done.hasCachedInput, false);
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
  assert.deepEqual(await fsp.readdir(f.outputDir), []);
  assert.equal(done.hasCachedInput, true);
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

test("changing output folders copies a validated duplicate into the selected folder and reuses it on return", async (t) => {
  const f = await fixture(t);
  const first = await f.result(f.manager.enqueue(CHART).id);
  const firstStat = await fsp.stat(first.outputPath);
  assert.equal(first.outputAvailable, true);
  assert.equal(first.inOutputDir, true);
  const secondFolder = path.join(f.directory, "FeedBack library");
  f.manager.setOutputDir(secondFolder);
  assert.equal(f.manager.snapshot()[0].outputAvailable, true, "A file in the old folder remains available.");
  assert.equal(f.manager.snapshot()[0].inOutputDir, false);
  const duplicate = await f.result(f.manager.enqueue(CHART).id);
  assert.equal(duplicate.state, "completed");
  assert.equal(duplicate.outputAvailable, true);
  assert.equal(duplicate.duplicateOf, first.id);
  assert.equal(duplicate.inOutputDir, true);
  assert.equal(path.dirname(duplicate.outputPath), secondFolder);
  assert.deepEqual(await fsp.readFile(duplicate.outputPath), FEEDPAK);
  assert.deepEqual(await fsp.readFile(first.outputPath), FEEDPAK);
  assert.equal((await fsp.stat(first.outputPath)).mtimeMs, firstStat.mtimeMs);
  f.manager.setOutputDir(f.outputDir);
  const returned = await f.result(f.manager.enqueue(CHART).id);
  assert.equal(returned.outputPath, first.outputPath);
  assert.equal(f.calls.filter((args) => args[1] === "-o").length, 1);
  assert.deepEqual(await fsp.readdir(f.outputDir), [path.basename(first.outputPath)]);
});

test("output preflight rejects an unusable folder before any download and selection leaves the old destination", async (t) => {
  const f = await fixture(t);
  const blocked = path.join(f.directory, "blocked");
  await fsp.writeFile(blocked, "preserve me");
  assert.throws(() => f.manager.setOutputDir(blocked), /output folder cannot safely save/);
  assert.equal(f.manager.outputDir, f.outputDir);
  assert.equal(await fsp.readFile(blocked, "utf8"), "preserve me");
  f.manager.outputDir = blocked;
  const failed = await f.result(f.manager.enqueue(CHART).id);
  assert.equal(failed.state, "failed");
  assert.equal(f.downloads.length, 0);
  assert.equal(f.calls.length, 0);
  assert.throws(() => f.manager.validateOutputDir(path.join(f.root, "unsafe-output")), /temporary storage/);
});

test("output preflight checks actual hard-link support and cleans its exact probes", async (t) => {
  const f = await fixture(t);
  const link = fs.linkSync;
  fs.linkSync = () => { throw Object.assign(new Error("filesystem does not support links"), { code: "ENOTSUP" }); };
  try { assert.throws(() => f.manager.validateOutputDir(), /hard-link support/); }
  finally { fs.linkSync = link; }
  assert.deepEqual(await fsp.readdir(f.outputDir), []);
});

test("ledger failures in asynchronous progress callbacks never interrupt a successful publication", async (t) => {
  const f = await fixture(t, { download: async (chart, args, normal) => {
    const rename = fs.renameSync;
    fs.renameSync = (from, to) => {
      if (to === path.join(f.root, "jobs.json")) throw Object.assign(new Error("temporary ledger denial"), { code: "EACCES" });
      return rename(from, to);
    };
    try {
      assert.doesNotThrow(() => args.onProgress(25));
      assert.doesNotThrow(() => args.onAttention("Host needs attention"));
      assert.match(f.manager.snapshot()[0].warning, /history could not be saved/);
      return await normal(chart, args);
    } finally { fs.renameSync = rename; }
  } });
  const done = await f.result(f.manager.enqueue(CHART).id);
  assert.equal(done.state, "completed");
  assert.deepEqual(await fsp.readFile(done.outputPath), FEEDPAK);
  assert.equal(done.warning, undefined);
});

test("a committed FeedPak survives final ledger failure and a receipt reconciles it on restart", async (t) => {
  const f = await fixture(t);
  const rename = fs.renameSync;
  fs.renameSync = (from, to) => {
    if (to === f.manager.ledger && f.manager.jobs.some((job) => job.committed)) throw Object.assign(new Error("ledger disk full"), { code: "ENOSPC" });
    return rename(from, to);
  };
  let done;
  try { done = await f.result(f.manager.enqueue(CHART).id); }
  finally { fs.renameSync = rename; }
  assert.equal(done.state, "completed");
  assert.match(done.warning, /history could not be saved/);
  assert.deepEqual(await fsp.readFile(done.outputPath), FEEDPAK);
  assert.ok((await fsp.readdir(f.root)).includes(`${done.id}.receipt.json`));
  await f.manager.dispose();
  let downloads = 0;
  const recovered = new SongJobs({ root: f.root, outputDir: f.outputDir,
    download: async () => { downloads++; }, runConverter: async () => {},
  });
  t.after(() => recovered.dispose());
  assert.equal(recovered.snapshot()[0].state, "completed");
  assert.match(recovered.snapshot()[0].message, /recovered after restart/);
  assert.equal(recovered.snapshot()[0].outputPath, done.outputPath);
  assert.equal(downloads, 0);
  assert.equal(fs.existsSync(path.join(f.root, `${done.id}.receipt.json`)), false);
});

test("restart receipt refuses a changed output and never removes it", async (t) => {
  const f = await fixture(t);
  const done = await f.result(f.manager.enqueue(CHART).id);
  await f.manager.dispose();
  const row = { ...done, state: "validating" };
  await fsp.writeFile(f.manager.ledger, JSON.stringify({ version: 1, jobs: [row] }));
  await fsp.writeFile(path.join(f.root, `${done.id}.receipt.json`), JSON.stringify({ version: 1, job: done }));
  await fsp.writeFile(done.outputPath, "a user changed this file");
  const recovered = new SongJobs({ root: f.root, outputDir: f.outputDir, download: async () => {}, runConverter: async () => {} });
  t.after(() => recovered.dispose());
  assert.equal(recovered.snapshot()[0].state, "failed");
  assert.equal(await fsp.readFile(done.outputPath, "utf8"), "a user changed this file");
});

test("conversion failure retains an inspected PSARC and retry uses that copy without downloading", async (t) => {
  let fail = true;
  const f = await fixture(t, { runConverter: async (args, _context, normal) => {
    if (args[1] === "-o" && fail) return { code: 1, stdout: "", stderr: "encoder temporarily unavailable" };
    return normal(args);
  } });
  const failed = await f.result(f.manager.enqueue(CHART).id);
  assert.equal(failed.state, "failed");
  assert.equal(failed.canRetry, true);
  assert.equal(failed.hasCachedInput, true);
  const cached = await f.manager.getCachedInput(failed.id);
  assert.deepEqual(await fsp.readFile(cached), PSARC);
  assert.equal(fs.existsSync(path.join(f.root, failed.id)), false);
  fail = false;
  const retried = await f.result(f.manager.retry(failed.id).id);
  assert.equal(retried.state, "completed");
  assert.equal(retried.hasCachedInput, false);
  assert.equal(f.downloads.length, 1);
  assert.deepEqual(await fsp.readFile(retried.outputPath), FEEDPAK);
  await f.manager.clearCache(failed.id);
  assert.equal(fs.existsSync(cached), false);
  assert.equal(f.manager.snapshot().find((job) => job.id === failed.id).hasCachedInput, false);
});

test("bad host response retry fetches afresh with metadata only, while cancelled input is removed", async (t) => {
  let attempts = 0;
  const f = await fixture(t, { download: async (chart, args, normal) => {
    attempts++;
    if (attempts === 1) {
      await fsp.writeFile(args.destination, "<html>A login page that is not a valid PSARC download</html>");
      return args.destination;
    }
    assert.deepEqual(Object.keys(chart).sort(), ["artist", "creator", "host", "id", "supported", "title"]);
    return normal(chart, args);
  } });
  const failed = await f.result(f.manager.enqueue({ ...CHART, url: "https://host.test/private?secret=yes" }).id);
  assert.equal(failed.hasCachedInput, false);
  await assert.rejects(() => f.manager.getCachedInput(failed.id), /no retained PSARC/);
  assert.equal((await f.result(f.manager.retry(failed.id).id)).state, "completed");
  assert.equal(attempts, 2);
});

test("retained cache is available after restart, but changed files are rejected before retry conversion", async (t) => {
  const f = await fixture(t, { preview: { song_count: 2, is_multi_song: true } });
  const failed = await f.result(f.manager.enqueue(CHART).id);
  const cached = await f.manager.getCachedInput(failed.id);
  await f.manager.dispose();
  let downloads = 0, conversions = 0;
  const recovered = new SongJobs({ root: f.root, outputDir: f.outputDir,
    download: async () => { downloads++; }, runConverter: async () => { conversions++; },
  });
  t.after(() => recovered.dispose());
  assert.equal(recovered.snapshot()[0].hasCachedInput, true);
  assert.equal(await recovered.getCachedInput(failed.id), cached);
  await fsp.writeFile(cached, Buffer.alloc(PSARC.length, 8));
  await assert.rejects(() => recovered.getCachedInput(failed.id), /changed or is incomplete/);
  const retry = recovered.retry(failed.id);
  await recovered.jobs.find((job) => job.id === retry.id).done;
  assert.equal(recovered.snapshot()[0].state, "failed");
  assert.equal(downloads, 0);
  assert.equal(conversions, 0);
});

test("cache retention evicts oldest owned files while preserving unrelated files and respecting its age limit", async (t) => {
  const f = await fixture(t, { preview: { song_count: 2, is_multi_song: true } });
  const failed = [];
  for (let index = 0; index < 4; index++) failed.push(await f.result(f.manager.enqueue({ ...CHART, id: String(42 + index) }).id));
  assert.equal(f.manager.snapshot().filter((job) => job.hasCachedInput).length, 3);
  assert.equal(f.manager.snapshot().find((job) => job.id === failed[0].id).hasCachedInput, false);
  const unrelated = path.join(f.root, "cache", "user-source.psarc");
  await fsp.writeFile(unrelated, "keep this");
  for (const job of f.manager.jobs) if (job.cacheHash) job.cacheAt = Date.now() - 8 * 24 * 60 * 60 * 1000;
  f.manager._pruneCache();
  assert.equal(f.manager.snapshot().filter((job) => job.hasCachedInput).length, 0);
  assert.equal(await fsp.readFile(unrelated, "utf8"), "keep this");
});

test("cancellation after inspection removes its retained input and waits for the converter", async (t) => {
  const entered = deferred(), release = deferred();
  const f = await fixture(t, { runConverter: async (args, _context, normal) => {
    if (args[1] === "-o") { entered.resolve(); await release.promise; }
    return normal(args);
  } });
  const job = f.manager.enqueue(CHART);
  await entered.promise;
  assert.equal(f.manager.snapshot()[0].hasCachedInput, true);
  const cancellation = f.manager.cancel(job.id);
  release.resolve();
  const done = await cancellation;
  assert.equal(done.state, "cancelled");
  assert.equal(done.hasCachedInput, false);
  assert.deepEqual(await fsp.readdir(path.join(f.root, "cache")), []);
  assert.deepEqual(await fsp.readdir(f.outputDir), []);
});

test("retry pins its cached source before a full history can evict it", async (t) => {
  let fail = true;
  const f = await fixture(t, { runConverter: async (args, _context, normal) => {
    if (args[1] === "-o" && fail) return { code: 1, stdout: "", stderr: "temporary failure" };
    return normal(args);
  } });
  const failed = await f.result(f.manager.enqueue(CHART).id);
  for (let index = 0; index < 99; index++) f.manager.jobs.push({
    id: crypto.randomUUID(), chartId: String(100 + index), title: "Older failed chart", artist: "An artist",
    state: "failed", createdAt: Date.now(), updatedAt: Date.now(),
  });
  f.manager._persist();
  assert.equal(f.manager.jobs.length, 100);
  fail = false;
  const retry = f.manager.retry(failed.id);
  assert.equal(f.manager.jobs.length, 100);
  assert.ok(f.manager.jobs.some((job) => job.id === failed.id));
  assert.equal((await f.result(retry.id)).state, "completed");
  assert.equal(f.downloads.length, 1);
});

test("a deleted completed output is no longer ready in the selected folder", async (t) => {
  const f = await fixture(t);
  const done = await f.result(f.manager.enqueue(CHART).id);
  assert.equal(done.outputAvailable, true);
  assert.equal(done.inOutputDir, true);
  await fsp.unlink(done.outputPath);
  const missing = f.manager.snapshot()[0];
  assert.equal(missing.outputAvailable, false);
  assert.equal(missing.inOutputDir, false);
  assert.equal(missing.state, "completed", "Missing output must not rewrite completed history.");
  assert.equal(missing.canRetry, false);
  assert.equal(f.downloads.length, 1, "Reading missing-output state must not automatically download again.");
  assert.equal((await f.result(f.manager.enqueue(CHART).id)).state, "completed");
  assert.equal(f.calls.filter((args) => args[1] === "-o").length, 2);
});

test("a moved completed output is unavailable while the moved file is preserved", async (t) => {
  const f = await fixture(t);
  const done = await f.result(f.manager.enqueue(CHART).id);
  const moved = path.join(f.outputDir, "renamed-by-user.feedpak");
  await fsp.rename(done.outputPath, moved);
  const snapshot = f.manager.snapshot()[0];
  assert.equal(snapshot.outputAvailable, false);
  assert.equal(snapshot.inOutputDir, false);
  assert.equal(snapshot.state, "completed");
  assert.equal(snapshot.canRetry, false);
  assert.deepEqual(await fsp.readFile(moved), FEEDPAK);
  assert.equal(f.downloads.length, 1);
});

test("a completed output replaced by a file symlink is unavailable", async (t) => {
  const f = await fixture(t);
  const done = await f.result(f.manager.enqueue(CHART).id);
  const target = path.join(f.outputDir, "kept-original.feedpak");
  await fsp.rename(done.outputPath, target);
  try { await fsp.symlink(target, done.outputPath, "file"); }
  catch (error) {
    if (process.platform === "win32" && error.code === "EPERM") { t.skip("File symlinks require Windows Developer Mode or the corresponding privilege."); return; }
    throw error;
  }
  const snapshot = f.manager.snapshot()[0];
  assert.equal(snapshot.outputAvailable, false);
  assert.equal(snapshot.inOutputDir, false);
  assert.equal(snapshot.state, "completed");
  assert.equal(snapshot.canRetry, false);
  assert.deepEqual(await fsp.readFile(target), FEEDPAK);
  assert.equal(f.downloads.length, 1);
});

test("Windows native path identity survives redirected profiles without weakening containment", { skip: process.platform !== "win32" }, async (t) => {
  const directory = await fsp.mkdtemp(path.join(os.tmpdir(), "feedforge-native-path-"));
  const physicalProfile = path.join(directory, "native-profile");
  const logicalProfile = path.join(directory, "roaming-profile");
  const physicalRoot = path.join(physicalProfile, "jobs");
  const logicalRoot = path.join(logicalProfile, "jobs");
  const outputDir = path.join(directory, "output");
  await fsp.mkdir(physicalRoot, { recursive: true });
  await fsp.symlink(physicalProfile, logicalProfile, "junction");
  const escapedDirectory = path.join(directory, "outside-job");
  await fsp.mkdir(escapedDirectory);
  const escapedFile = path.join(escapedDirectory, "source.psarc");
  await fsp.writeFile(escapedFile, PSARC);

  // MSIX app-data redirection can leave the legacy JavaScript resolver at
  // a logical profile path while the native and async resolvers return the
  // backing directory. A real junction provides the native filesystem side;
  // this narrowly scoped shim supplies the observed logical resolver result.
  const realpath = fs.realpathSync;
  function logicalRealpath(filename, options) {
    const resolved = path.resolve(String(filename));
    if (resolved === logicalProfile || resolved.startsWith(logicalProfile + path.sep)) {
      fs.lstatSync(resolved);
      return options?.encoding === "buffer" ? Buffer.from(resolved) : resolved;
    }
    return realpath(filename, options);
  }
  logicalRealpath.native = realpath.native;
  fs.realpathSync = logicalRealpath;
  let manager;
  t.after(async () => {
    try { if (manager) await manager.dispose(); }
    finally {
      fs.realpathSync = realpath;
      // Both junction targets and the simulated escape are inside this new,
      // test-owned directory; no profile, user input or library is touched.
      assert.equal(path.dirname(directory), path.resolve(os.tmpdir()));
      assert.match(path.basename(directory), /^feedforge-native-path-/);
      await fsp.rm(directory, { recursive: true, force: true });
    }
  });
  assert.equal(fs.realpathSync(logicalRoot), logicalRoot);
  assert.equal(fs.realpathSync.native(logicalRoot), physicalRoot);
  assert.equal(await fsp.realpath(logicalRoot), physicalRoot);

  let failConversion = true, returnEscapedInput = false, downloads = 0;
  const calls = [];
  manager = new SongJobs({ root: logicalRoot, outputDir,
    download: async (_chart, args) => {
      downloads++;
      if (returnEscapedInput) {
        const junction = path.join(args.directory, "escaped");
        await fsp.symlink(escapedDirectory, junction, "junction");
        return path.join(junction, "source.psarc");
      }
      await fsp.writeFile(args.destination, PSARC);
      // The browser returns its assigned destination verbatim; the queue's
      // subsequent async realpath check sees the native backing directory.
      return args.destination;
    },
    runConverter: async (args) => {
      calls.push(args);
      if (args[0] === "--inspect-json") return inspect();
      if (args[0] === "--validate-feedpak") return { code: 0, stderr: "", stdout: JSON.stringify({ ok: true,
        results: [{ input_path: args[1], validation: { ok: true, errors: [] } }],
      }) };
      if (failConversion) return { code: 1, stdout: "", stderr: "temporary encoder failure" };
      await fsp.writeFile(args[2], FEEDPAK);
      return { code: 0, stdout: "Converted", stderr: "" };
    },
  });
  const finish = async (queued) => {
    await manager.jobs.find((job) => job.id === queued.id).done;
    return manager.snapshot().find((job) => job.id === queued.id);
  };

  const failed = await finish(manager.enqueue(CHART));
  assert.equal(failed.state, "failed");
  assert.match(failed.error, /temporary encoder failure/, "A valid native-path download must reach conversion, not fail logical-path containment.");
  assert.equal(manager.root, physicalRoot);
  assert.equal(failed.hasCachedInput, true);
  const retained = await manager.getCachedInput(failed.id);
  assert.equal(path.dirname(retained), path.join(physicalRoot, "cache"));
  assert.deepEqual(await fsp.readFile(retained), PSARC);
  assert.equal(fs.existsSync(path.join(physicalRoot, failed.id)), false, "The failed attempt's native UUID folder must be cleaned.");
  assert.throws(() => manager.validateOutputDir(path.join(logicalRoot, "cache")), /temporary storage/, "A logical alias must not bypass output containment.");

  failConversion = false;
  const completed = await finish(manager.retry(failed.id));
  assert.equal(completed.state, "completed", completed.error);
  assert.equal(completed.inOutputDir, true);
  assert.equal(downloads, 1, "Retry must use the verified retained native-path PSARC.");
  assert.equal(fs.existsSync(path.join(physicalRoot, completed.id)), false);
  const before = await fsp.stat(completed.outputPath);
  const reused = await finish(manager.enqueue(CHART));
  assert.equal(reused.state, "completed", reused.error);
  assert.equal(reused.outputPath, completed.outputPath);
  assert.equal((await fsp.stat(completed.outputPath)).mtimeMs, before.mtimeMs);
  const secondOutput = path.join(directory, "second-output");
  manager.setOutputDir(secondOutput);
  const copied = await finish(manager.enqueue(CHART));
  assert.equal(copied.state, "completed", copied.error);
  assert.equal(path.dirname(copied.outputPath), secondOutput);
  assert.equal(copied.outputHash, completed.outputHash);
  assert.deepEqual(await fsp.readFile(copied.outputPath), FEEDPAK);
  assert.equal(calls.filter((args) => args[1] === "-o").length, 2, "Only the initial failed conversion and cached successful retry may convert.");

  returnEscapedInput = true;
  const callsBeforeEscape = calls.length;
  const escaped = await finish(manager.enqueue(CHART));
  assert.equal(escaped.state, "failed");
  assert.match(escaped.error, /not a regular file in its job folder/);
  assert.equal(escaped.hasCachedInput, false);
  assert.equal(calls.length, callsBeforeEscape, "Native containment must reject a junction escape before invoking the converter.");
  assert.deepEqual(await fsp.readFile(escapedFile), PSARC, "Cleanup must not follow the escaped junction and delete its target.");
  assert.equal(fs.existsSync(path.join(physicalRoot, escaped.id)), false);
  await manager.clearCache(failed.id);
  assert.deepEqual(await fsp.readdir(path.join(physicalRoot, "cache")), []);
  assert.deepEqual(await fsp.readdir(physicalRoot), ["cache", "jobs.json"], "Every owned UUID folder must be cleaned after settlement.");
});
