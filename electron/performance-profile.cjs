"use strict";

const os = require("os");
const fs = require("fs");

const GIB = 1024 ** 3;
const AUTO_WORKER_CEILING = 24;
const MANUAL_WORKER_CEILING = 32;

function positiveInteger(value, fallback = 1) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) return fallback;
  return Math.max(1, Math.floor(parsed));
}

function positiveBytes(value, fallback = GIB) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) return fallback;
  return parsed;
}

function computePerformanceProfile({ logicalProcessors, totalMemoryBytes } = {}) {
  const processors = positiveInteger(logicalProcessors);
  const memoryBytes = positiveBytes(totalMemoryBytes);
  // Installed memory is commonly reported a little below its marketed size.
  // Rounding first keeps a 31.9 GiB machine in the expected 32 GiB tier.
  const memoryGb = Math.max(1, Math.round(memoryBytes / GIB));
  const recommendedWorkers = Math.max(1, Math.min(
    AUTO_WORKER_CEILING,
    Math.floor(processors / 2),
    Math.floor(memoryGb / 2.5)
  ));
  const manualMaxWorkers = Math.max(recommendedWorkers, Math.min(
    MANUAL_WORKER_CEILING,
    processors,
    Math.floor(memoryGb / 2)
  ));

  return {
    logicalProcessors: processors,
    totalMemoryBytes: memoryBytes,
    memoryGb,
    recommendedWorkers,
    manualMaxWorkers
  };
}

function detectedLogicalProcessors(osApi) {
  try {
    if (typeof osApi.availableParallelism === "function") {
      return positiveInteger(osApi.availableParallelism());
    }
  } catch {
    // Fall through to the portable CPU list.
  }
  try {
    const processors = osApi.cpus?.();
    if (Array.isArray(processors) && processors.length) return processors.length;
  } catch {
    // The final fallback is intentionally conservative.
  }
  return 1;
}

function detectedTotalMemoryBytes(osApi, processApi) {
  let hostBytes = GIB;
  try {
    hostBytes = positiveBytes(osApi.totalmem?.());
  } catch {
    // Keep the conservative fallback.
  }
  try {
    const constrainedBytes = Number(processApi?.constrainedMemory?.());
    if (Number.isFinite(constrainedBytes) && constrainedBytes > 0) {
      return Math.min(hostBytes, constrainedBytes);
    }
  } catch {
    // A missing or unsupported constraint API means host memory is usable.
  }
  return hostBytes;
}

function linuxMemAvailableBytes(fsApi = fs) {
  try {
    const contents = fsApi.readFileSync("/proc/meminfo", "utf8");
    const match = String(contents).match(/^MemAvailable:\s+(\d+)\s+kB$/im);
    if (!match) return null;
    const availableBytes = Number(match[1]) * 1024;
    return Number.isFinite(availableBytes) && availableBytes >= 0 ? availableBytes : null;
  } catch {
    return null;
  }
}

function detectedAvailableMemoryBytes(
  osApi,
  processApi,
  totalMemoryBytes,
  { platform = process.platform, fsApi = fs } = {}
) {
  let processAvailable = null;
  try {
    const availableBytes = Number(processApi?.availableMemory?.());
    if (Number.isFinite(availableBytes) && availableBytes >= 0) {
      processAvailable = availableBytes;
    }
  } catch {
    // Continue with platform-native telemetry.
  }

  if (platform === "linux") {
    const memAvailable = linuxMemAvailableBytes(fsApi);
    if (memAvailable !== null) {
      let constrainedBytes = 0;
      try {
        constrainedBytes = Number(processApi?.constrainedMemory?.()) || 0;
      } catch {
        constrainedBytes = 0;
      }
      return Math.min(
        totalMemoryBytes,
        memAvailable,
        constrainedBytes > 0 && processAvailable !== null ? processAvailable : totalMemoryBytes
      );
    }
  }

  if (processAvailable !== null) return Math.min(totalMemoryBytes, processAvailable);
  try {
    if (typeof osApi.freemem !== "function") return totalMemoryBytes;
    return Math.min(totalMemoryBytes, Math.max(0, Number(osApi.freemem()) || 0));
  } catch {
    return totalMemoryBytes;
  }
}

function detectPerformanceProfile(osApi = os, processApi = process) {
  return computePerformanceProfile({
    logicalProcessors: detectedLogicalProcessors(osApi),
    totalMemoryBytes: detectedTotalMemoryBytes(osApi, processApi)
  });
}

function computeMemoryStatus({ totalMemoryBytes, freeMemoryBytes } = {}) {
  const totalBytes = positiveBytes(totalMemoryBytes);
  const freeBytes = Math.max(0, Math.min(totalBytes, Number(freeMemoryBytes) || 0));
  const lowerMemoryMachine = totalBytes < 12 * GIB;
  const criticalFloor = lowerMemoryMachine ? 1 * GIB : 2 * GIB;
  const resumeFloor = lowerMemoryMachine ? 1.5 * GIB : 3 * GIB;
  const criticalThresholdBytes = Math.min(totalBytes * 0.8, Math.max(criticalFloor, totalBytes * 0.08));
  const resumeThresholdBytes = Math.min(totalBytes * 0.95, Math.max(
    resumeFloor,
    totalBytes * 0.12,
    criticalThresholdBytes + 0.25 * GIB
  ));

  return {
    totalMemoryBytes: totalBytes,
    freeMemoryBytes: freeBytes,
    criticalThresholdBytes,
    resumeThresholdBytes,
    critical: freeBytes <= criticalThresholdBytes,
    canResume: freeBytes >= resumeThresholdBytes
  };
}

function detectMemoryStatus(osApi = os, processApi = process, environment = {}) {
  const totalMemoryBytes = detectedTotalMemoryBytes(osApi, processApi);
  return computeMemoryStatus({
    totalMemoryBytes,
    freeMemoryBytes: detectedAvailableMemoryBytes(osApi, processApi, totalMemoryBytes, environment)
  });
}

function clampRequestedWorkers(value, profile = detectPerformanceProfile()) {
  const requested = Number(value);
  const fallback = positiveInteger(profile?.recommendedWorkers);
  const maximum = Math.min(
    MANUAL_WORKER_CEILING,
    positiveInteger(profile?.manualMaxWorkers, fallback)
  );
  if (!Number.isFinite(requested) || requested <= 0) return Math.min(fallback, maximum);
  return Math.max(1, Math.min(Math.floor(requested), maximum));
}

module.exports = {
  AUTO_WORKER_CEILING,
  GIB,
  MANUAL_WORKER_CEILING,
  clampRequestedWorkers,
  computeMemoryStatus,
  computePerformanceProfile,
  detectMemoryStatus,
  detectPerformanceProfile,
  detectedAvailableMemoryBytes,
  detectedTotalMemoryBytes,
  detectedLogicalProcessors,
  linuxMemAvailableBytes
};
