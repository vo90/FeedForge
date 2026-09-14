"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const {
  DECODER_VERIFIED_ENV,
  TOOLS_DIRECTORY_ENV,
  converterEnvironment,
  createAudioDecoderStatusCache,
  decoderExecutableName,
  resolveAudioDecoder
} = require("../../electron/audio-dependency.cjs");

function temporaryDirectory(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "feedforge-audio-dependency-"));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  return directory;
}

function writePlaceholderExecutable(directory) {
  fs.mkdirSync(directory, { recursive: true });
  const executablePath = path.join(directory, decoderExecutableName());
  fs.writeFileSync(executablePath, "placeholder", "utf8");
  return executablePath;
}

function successfulProbe(_command, _args, options) {
  return {
    status: 1,
    signal: null,
    stdout: "vgmstream CLI decoder r2117\nUsage: vgmstream-cli",
    stderr: "",
    options
  };
}

test("source development mode discovers the complete versioned runtime bundle", (t) => {
  const appPath = temporaryDirectory(t);
  const runtimeTools = path.join(appPath, "runtime", "vgmstream-r2117");
  const expectedExecutable = writePlaceholderExecutable(runtimeTools);
  writePlaceholderExecutable(path.join(appPath, "src", "feedback_converter", "tools"));

  const status = resolveAudioDecoder({
    appPath,
    resourcesPath: appPath,
    isPackaged: false,
    platform: process.platform,
    environment: { PATH: "" },
    spawnSyncApi: successfulProbe
  });

  assert.equal(status.ready, true);
  assert.equal(status.source, "source-runtime");
  assert.equal(status.executablePath, expectedExecutable);
  assert.equal(status.toolsDirectory, runtimeTools);
});

test("packaged mode discovers the decoder inside the bundled converter", (t) => {
  const resourcesPath = temporaryDirectory(t);
  const toolsDirectory = path.join(
    resourcesPath,
    "bin",
    "psarc2feedpak",
    "_internal",
    "feedback_converter",
    "tools"
  );
  writePlaceholderExecutable(toolsDirectory);

  const status = resolveAudioDecoder({
    appPath: path.join(resourcesPath, "app.asar"),
    resourcesPath,
    isPackaged: true,
    platform: process.platform,
    environment: { PATH: "" },
    spawnSyncApi: successfulProbe
  });

  assert.equal(status.ready, true);
  assert.equal(status.source, "packaged-converter");
  assert.equal(status.toolsDirectory, toolsDirectory);
});

test("an explicit tools override wins and is passed to converter children", (t) => {
  const appPath = temporaryDirectory(t);
  const configuredTools = path.join(appPath, "configured-vgmstream");
  writePlaceholderExecutable(configuredTools);

  const status = resolveAudioDecoder({
    appPath,
    resourcesPath: appPath,
    isPackaged: false,
    platform: process.platform,
    environment: { [TOOLS_DIRECTORY_ENV]: configuredTools, PATH: "C:\\existing" },
    spawnSyncApi: successfulProbe
  });
  const childEnvironment = converterEnvironment({ PATH: "C:\\existing" }, status, process.platform);

  assert.equal(status.source, "environment");
  assert.equal(childEnvironment[TOOLS_DIRECTORY_ENV], configuredTools);
  assert.equal(childEnvironment[DECODER_VERIFIED_ENV], "1");
  assert.equal(childEnvironment.PATH.split(path.delimiter)[0], configuredTools);
});

test("a decoder that cannot start fails closed with a non-destructive message", (t) => {
  const appPath = temporaryDirectory(t);
  const toolsDirectory = path.join(appPath, "runtime", "vgmstream-r2117");
  writePlaceholderExecutable(toolsDirectory);

  const status = resolveAudioDecoder({
    appPath,
    resourcesPath: appPath,
    isPackaged: false,
    platform: process.platform,
    environment: { PATH: "" },
    spawnSyncApi: () => ({
      status: 3221225781,
      signal: null,
      stdout: "",
      stderr: "A required DLL was not found."
    })
  });

  assert.equal(status.ready, false);
  assert.equal(status.available, true);
  assert.match(status.message, /could not start/);
  assert.match(status.message, /conversion was not started/);
  assert.match(status.message, /no WEM-only FeedPaks were created/);
  const childEnvironment = converterEnvironment({
    [DECODER_VERIFIED_ENV]: "1",
    PATH: "C:\\existing"
  }, status, process.platform);
  assert.equal(childEnvironment[DECODER_VERIFIED_ENV], undefined);
});

test("a loader error containing the executable name cannot pass the smoke test", (t) => {
  const appPath = temporaryDirectory(t);
  writePlaceholderExecutable(path.join(appPath, "runtime", "vgmstream-r2117"));

  const status = resolveAudioDecoder({
    appPath,
    resourcesPath: appPath,
    isPackaged: false,
    platform: process.platform,
    environment: { PATH: "" },
    spawnSyncApi: () => ({
      status: 127,
      signal: null,
      stdout: "",
      stderr: "/opt/vgmstream-cli: error while loading shared libraries: libavcodec.so: cannot open shared object file"
    })
  });

  assert.equal(status.ready, false);
  assert.equal(status.available, true);
  assert.equal(converterEnvironment({}, status)[DECODER_VERIFIED_ENV], undefined);
});

test("one successful preflight is reused across a four-thousand-song batch", (t) => {
  const executablePath = writePlaceholderExecutable(temporaryDirectory(t));
  let resolverCalls = 0;
  const cache = createAudioDecoderStatusCache({
    resolver: () => {
      resolverCalls += 1;
      return {
        ready: true,
        executablePath,
        toolsDirectory: path.dirname(executablePath)
      };
    }
  });

  for (let index = 0; index < 4000; index += 1) {
    assert.equal(cache.get().ready, true);
  }
  assert.equal(resolverCalls, 1);

  cache.get({}, { refresh: true });
  assert.equal(resolverCalls, 2);
});
