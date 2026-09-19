"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { EventEmitter } = require("node:events");
const { PassThrough } = require("node:stream");
const test = require("node:test");
const { redactConverterArgs } = require("../../electron/converter-args.cjs");
const { converterEnvironment, DECODER_VERIFIED_ENV, TOOLS_DIRECTORY_ENV } = require("../../electron/audio-dependency.cjs");

test("redacts separate, equals-style, empty and repeated API keys without changing other arguments", () => {
  const args = Object.freeze([
    "--demucs-url", "http://127.0.0.1:7865", "--demucs-api-key", "key with spaces",
    "--demucs-api-key=other=value", "--demucs-api-key=", "song.psarc"
  ]);
  assert.deepEqual(redactConverterArgs(args), [
    "--demucs-url", "http://127.0.0.1:7865", "--demucs-api-key", "[redacted]",
    "--demucs-api-key=[redacted]", "--demucs-api-key=[redacted]", "song.psarc"
  ]);
  assert.equal(args[3], "key with spaces");
  assert.deepEqual(redactConverterArgs(["--demucs-api-key"]), ["--demucs-api-key"]);
  assert.deepEqual(redactConverterArgs(), []);
});

test("actual shared converter launch logs a redacted copy but spawns the original arguments and environment", async () => {
  const source = fs.readFileSync(path.resolve(__dirname, "../../electron/main.cjs"), "utf8");
  const match = source.match(/function runConverterProcess\([\s\S]*?(?=\n(?:async )?function )/);
  assert.ok(match, "shared converter launcher exists");
  const records = [];
  const launches = [];
  const environment = Object.freeze({ TEST_MARKER: "unchanged", PYTHONPATH: "older-source", PATH: "system-tools" });
  const appRoot = path.resolve("fixture");
  const temporaryDirectory = path.resolve("job-temp");
  const decoderDirectory = path.resolve("decoder");
  const child = new EventEmitter();
  child.stdout = new PassThrough();
  child.stderr = new PassThrough();
  const output = JSON.stringify({ title: "Björk — Jóga 🎸" });
  const diagnostic = "Preparing Björk — Jóga 🎸";
  const stderrLines = [];
  const sandbox = {
    converterCommand: () => ({ command: "python", prefix: ["-m", "feedback_converter"], cwd: "/fixture" }),
    getAudioDecoderStatus: () => ({ ready: true, available: true, toolsDirectory: decoderDirectory }),
    converterEnvironment, path,
    audioDecoderLogDetails: (value) => value,
    fs: { existsSync: () => true },
    app: { isPackaged: false, getAppPath: () => appRoot },
    process: { env: environment, platform: process.platform, resourcesPath: "/resources" },
    logDebug: (event, details) => records.push({ event, details }),
    redactConverterArgs, activeConverterChildren: new Set(), tail: (value) => value,
    isConverterProtocolLine: () => false,
    spawn: (command, args, options) => {
      launches.push({ command, args, options });
      queueMicrotask(() => {
        // Operating-system pipe chunks may split inside any UTF-8 code point.
        for (const byte of Buffer.from(output)) child.stdout.write(Buffer.from([byte]));
        for (const byte of Buffer.from(diagnostic + "\n")) child.stderr.write(Buffer.from([byte]));
        child.stdout.end(); child.stderr.end(); child.emit("close", 0);
      });
      return child;
    }
  };
  vm.createContext(sandbox);
  vm.runInContext(match[0], sandbox);
  const args = Object.freeze(["song.psarc", "--demucs-api-key", "secret-value", "--song-workers", "4"]);
  const result = await sandbox.runConverterProcess(args, { directory: temporaryDirectory, logOutput: false,
    env: { SONGSTERR_NODE_PATH: "fixture-node", ELECTRON_RUN_AS_NODE: "1", TEMP: "discarded-override" },
    onStderrLine: line => stderrLines.push(line) });
  assert.equal(result.stdout, output);
  assert.equal(result.stderr, diagnostic + "\n");
  assert.deepEqual(stderrLines, [diagnostic]);
  assert.equal(records.find(record => record.event === "converter.process.close").details.stdoutTail, "[structured response omitted]");
  const start = records.find((record) => record.event === "converter.process.start");
  assert.equal(JSON.stringify(start).includes("secret-value"), false);
  assert.equal(start.details.args[3], "--demucs-api-key");
  assert.equal(start.details.args[4], "[redacted]");
  assert.equal(launches[0].args[4], "secret-value");
  const childEnvironment = launches[0].options.env;
  assert.notEqual(childEnvironment, environment);
  assert.equal(childEnvironment.TEST_MARKER, "unchanged");
  assert.equal(childEnvironment.SONGSTERR_NODE_PATH, "fixture-node");
  assert.equal(childEnvironment.ELECTRON_RUN_AS_NODE, "1");
  assert.equal(childEnvironment.PYTHONPATH.split(path.delimiter)[0], path.join(appRoot, "src"));
  assert.equal(childEnvironment[TOOLS_DIRECTORY_ENV], decoderDirectory);
  assert.equal(childEnvironment[DECODER_VERIFIED_ENV], "1");
  for (const key of ["TEMP", "TMP", "TMPDIR"]) assert.equal(childEnvironment[key], temporaryDirectory);
  assert.equal(childEnvironment.PYTHONDONTWRITEBYTECODE, "1");
  assert.equal(environment.PYTHONPATH, "older-source");
  assert.equal(launches[0].options.windowsHide, true);
  assert.equal(args[2], "secret-value");
  assert.equal(sandbox.activeConverterChildren.size, 0);
});

test('normal PSARC conversion forwards difficulty only on explicit opt-in; FeedPak editing never does', async () => {
  const source = fs.readFileSync(path.resolve(__dirname, '../../electron/main.cjs'), 'utf8');
  const handlers = new Map(), requests = [];
  const reachedConverter = new Error('Fixture reached converter boundary');
  const sandbox = {
    ipcMain: { handle: (name, handler) => handlers.set(name, handler) },
    detectPerformanceProfile: () => ({}), clampRequestedWorkers: value => value,
    normalizeValidationPolicy: value => value || 'safe', conversionJobId: () => 'fixture-job',
    blockingAudioDecoderStatus: () => null, logDebug() {},
    runTrackedConverter: async (_event, payload, args, options) => {
      requests.push({ payload, args, options });
      throw reachedConverter;
    }
  };
  vm.createContext(sandbox);
  for (const channel of ['converter:convert', 'feedpak:update']) {
    const start = source.indexOf(`ipcMain.handle("${channel}"`);
    const end = source.indexOf('\nipcMain.handle(', start + 1);
    assert.ok(start >= 0 && end > start);
    vm.runInContext(source.slice(start, end), sandbox);
  }
  for (const generateDifficulty of [undefined, false, true, 'true']) {
    const payload = { inputPath: 'song.psarc', generateDifficulty, songWorkers: 3, bStandardTo7String: true };
    await assert.rejects(handlers.get('converter:convert')({}, payload), error => error === reachedConverter);
    const request = requests.at(-1);
    assert.equal(request.args.includes('--generate-difficulty'), generateDifficulty === true);
    assert.equal(request.args.includes('--b-standard-to-7-string'), true);
    assert.equal(request.options.admissionWeight, 3);
  }
  await assert.rejects(handlers.get('feedpak:update')({}, { inputPath: 'existing.feedpak', generateDifficulty: true }),
    error => error === reachedConverter);
  assert.equal(requests.at(-1).args.includes('--generate-difficulty'), false);
});
