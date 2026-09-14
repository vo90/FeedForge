"use strict";

const childProcess = require("child_process");
const fs = require("fs");
const path = require("path");

const TOOLS_DIRECTORY_ENV = "FEEDFORGE_TOOLS_DIR";
const DECODER_VERIFIED_ENV = "FEEDFORGE_VGMSTREAM_VERIFIED";
const PROBE_TIMEOUT_MS = 5000;

function decoderExecutableName(platform = process.platform) {
  return platform === "win32" ? "vgmstream-cli.exe" : "vgmstream-cli";
}

function isFile(fsApi, filePath) {
  if (!filePath) return false;
  try {
    return fsApi.statSync(filePath).isFile();
  } catch {
    return false;
  }
}

function isDirectory(fsApi, directory) {
  if (!directory) return false;
  try {
    return fsApi.statSync(directory).isDirectory();
  } catch {
    return false;
  }
}

function versionedBundleDirectories(runtimeRoot, fsApi = fs) {
  if (!isDirectory(fsApi, runtimeRoot)) return [];
  try {
    return fsApi.readdirSync(runtimeRoot, { withFileTypes: true })
      .filter((entry) => entry.isDirectory() && /^vgmstream(?:[-_].+)?$/i.test(entry.name))
      .map((entry) => path.join(runtimeRoot, entry.name))
      .sort((left, right) => right.localeCompare(left, undefined, { numeric: true }));
  } catch {
    return [];
  }
}

function configuredDirectory(value, executableName, fsApi = fs) {
  const configured = String(value || "").trim();
  if (!configured) return null;
  const resolved = path.resolve(configured);
  if (isFile(fsApi, resolved)) return path.dirname(resolved);
  if (isDirectory(fsApi, resolved)) return resolved;
  // Keep a missing configured directory in the candidate list so diagnostics
  // can distinguish a broken explicit override from normal auto-discovery.
  return path.basename(resolved).toLowerCase() === executableName.toLowerCase()
    ? path.dirname(resolved)
    : resolved;
}

function pathDirectories(environment = process.env) {
  const pathKey = Object.keys(environment || {}).find((key) => key.toUpperCase() === "PATH");
  const value = pathKey ? environment[pathKey] : "";
  return String(value || "").split(path.delimiter).map((entry) => entry.trim()).filter(Boolean);
}

function candidateToolDirectories({
  appPath = "",
  resourcesPath = "",
  isPackaged = false,
  platform = process.platform,
  environment = process.env,
  fsApi = fs
} = {}) {
  const executableName = decoderExecutableName(platform);
  const candidates = [];
  const seen = new Set();
  const add = (directory, source) => {
    if (!directory) return;
    const resolved = path.resolve(directory);
    const key = platform === "win32" ? resolved.toLowerCase() : resolved;
    if (seen.has(key)) return;
    seen.add(key);
    candidates.push({ directory: resolved, source });
  };

  add(configuredDirectory(environment?.[TOOLS_DIRECTORY_ENV], executableName, fsApi), "environment");

  if (isPackaged && resourcesPath) {
    const converterRoot = path.join(resourcesPath, "bin", "psarc2feedpak");
    add(path.join(converterRoot, "_internal", "feedback_converter", "tools"), "packaged-converter");
    add(path.join(converterRoot, "feedback_converter", "tools"), "packaged-converter");
    add(path.join(converterRoot, "tools"), "packaged-converter");
    add(path.join(resourcesPath, "bin", "feedback_converter", "tools"), "packaged-converter");
    add(path.join(resourcesPath, "vgmstream"), "packaged-resource");
    for (const directory of versionedBundleDirectories(path.join(resourcesPath, "runtime"), fsApi)) {
      add(directory, "packaged-resource");
    }
  }

  if (appPath) {
    for (const directory of versionedBundleDirectories(path.join(appPath, "runtime"), fsApi)) {
      add(directory, "source-runtime");
    }
    add(path.join(appPath, "src", "feedback_converter", "tools"), "source-tools");
    add(path.join(appPath, "dist", "psarc2feedpak", "_internal", "feedback_converter", "tools"), "local-converter");
    add(path.join(appPath, "dist", "psarc2feedpak", "feedback_converter", "tools"), "local-converter");
    add(path.join(appPath, "dist", "psarc2feedpak", "tools"), "local-converter");
  }

  for (const directory of pathDirectories(environment)) add(directory, "path");
  return candidates;
}

function environmentWithDecoder(baseEnvironment, toolsDirectory, platform = process.platform) {
  const environment = { ...(baseEnvironment || {}) };
  if (!toolsDirectory) return environment;

  const resolvedDirectory = path.resolve(toolsDirectory);
  environment[TOOLS_DIRECTORY_ENV] = resolvedDirectory;
  const pathKeys = Object.keys(environment).filter((key) => key.toUpperCase() === "PATH");
  const pathKey = pathKeys[0] || (platform === "win32" ? "Path" : "PATH");
  const existing = String(environment[pathKey] || "");
  for (const duplicate of pathKeys.slice(1)) delete environment[duplicate];
  const entries = existing.split(path.delimiter).map((entry) => entry.trim()).filter(Boolean);
  const normalizedDirectory = platform === "win32" ? resolvedDirectory.toLowerCase() : resolvedDirectory;
  const withoutDuplicate = entries.filter((entry) => {
    const resolved = path.resolve(entry);
    return (platform === "win32" ? resolved.toLowerCase() : resolved) !== normalizedDirectory;
  });
  environment[pathKey] = [resolvedDirectory, ...withoutDuplicate].join(path.delimiter);
  return environment;
}

function probeDecoder(executablePath, {
  platform = process.platform,
  environment = process.env,
  spawnSyncApi = childProcess.spawnSync,
  timeoutMs = PROBE_TIMEOUT_MS
} = {}) {
  const toolsDirectory = path.dirname(executablePath);
  let result;
  try {
    result = spawnSyncApi(executablePath, ["-h"], {
      cwd: toolsDirectory,
      env: environmentWithDecoder(environment, toolsDirectory, platform),
      encoding: "utf8",
      windowsHide: true,
      timeout: timeoutMs,
      maxBuffer: 1024 * 1024
    });
  } catch (error) {
    return { ready: false, error: error?.message || "The decoder could not be started." };
  }

  const output = `${result?.stdout || ""}\n${result?.stderr || ""}`.trim();
  const version = output.split(/\r?\n/)
    .map((line) => line.trim())
    .find((line) => /^vgmstream CLI decoder\b/i.test(line)) || "";
  const signatureFound = Boolean(version);
  const ready = !result?.error && !result?.signal && signatureFound;
  if (ready) return { ready: true, version, status: result?.status ?? null };

  const detail = result?.error?.message
    || (result?.signal ? `Decoder probe ended with ${result.signal}.` : "")
    || output.split(/\r?\n/).find(Boolean)
    || `Decoder probe exited with status ${result?.status ?? "unknown"}.`;
  return { ready: false, error: detail, status: result?.status ?? null };
}

function resolveAudioDecoder(options = {}) {
  const fsApi = options.fsApi || fs;
  const platform = options.platform || process.platform;
  const executableName = decoderExecutableName(platform);
  const candidates = candidateToolDirectories({ ...options, fsApi, platform });
  const failedProbes = [];

  for (const candidate of candidates) {
    const executablePath = path.join(candidate.directory, executableName);
    if (!isFile(fsApi, executablePath)) continue;
    const probe = probeDecoder(executablePath, options);
    if (probe.ready) {
      return {
        ready: true,
        available: true,
        executablePath,
        toolsDirectory: candidate.directory,
        source: candidate.source,
        version: probe.version,
        message: "WEM audio decoder is ready."
      };
    }
    failedProbes.push({ executablePath, error: probe.error || "Decoder probe failed." });
  }

  const configured = String(options.environment?.[TOOLS_DIRECTORY_ENV] || "").trim();
  const message = failedProbes.length
    ? "FeedForge found the WEM audio decoder, but it could not start with its required support files. PSARC conversion was not started, so no WEM-only FeedPaks were created. Restore or reinstall the complete vgmstream bundle, then check again."
    : "FeedForge could not find its WEM audio decoder. PSARC conversion was not started, so no WEM-only FeedPaks were created. Restore or reinstall the complete vgmstream bundle, then check again.";
  return {
    ready: false,
    available: failedProbes.length > 0,
    executablePath: failedProbes[0]?.executablePath || "",
    toolsDirectory: failedProbes[0] ? path.dirname(failedProbes[0].executablePath) : configured,
    source: "",
    version: "",
    message,
    error: failedProbes[0]?.error || "vgmstream-cli was not found.",
    failedProbes
  };
}

function converterEnvironment(baseEnvironment, decoderStatus, platform = process.platform) {
  if (!decoderStatus?.ready || !decoderStatus.toolsDirectory) {
    const environment = { ...(baseEnvironment || {}) };
    delete environment[DECODER_VERIFIED_ENV];
    return environment;
  }
  const environment = environmentWithDecoder(baseEnvironment, decoderStatus.toolsDirectory, platform);
  environment[DECODER_VERIFIED_ENV] = "1";
  return environment;
}

function createAudioDecoderStatusCache({
  fsApi = fs,
  resolver = resolveAudioDecoder
} = {}) {
  let cachedStatus = null;
  return {
    get(resolveOptions = {}, { refresh = false } = {}) {
      if (!refresh && cachedStatus) {
        if (!cachedStatus.ready || isFile(fsApi, cachedStatus.executablePath)) {
          return cachedStatus;
        }
      }
      cachedStatus = resolver(resolveOptions);
      return cachedStatus;
    },
    clear() {
      cachedStatus = null;
    },
    peek() {
      return cachedStatus;
    }
  };
}

module.exports = {
  DECODER_VERIFIED_ENV,
  PROBE_TIMEOUT_MS,
  TOOLS_DIRECTORY_ENV,
  candidateToolDirectories,
  converterEnvironment,
  createAudioDecoderStatusCache,
  decoderExecutableName,
  environmentWithDecoder,
  pathDirectories,
  probeDecoder,
  resolveAudioDecoder,
  versionedBundleDirectories
};
