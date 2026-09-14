"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {
  GIB,
  clampRequestedWorkers,
  computeMemoryStatus,
  computePerformanceProfile,
  detectMemoryStatus,
  detectPerformanceProfile
} = require("../../electron/performance-profile.cjs");

test("performance tiers derive adaptive recommendations and manual limits", () => {
  const tiers = [
    [4, 8, 2, 4],
    [8, 16, 4, 8],
    [16, 16, 6, 8],
    [24, 32, 12, 16],
    [32, 64, 16, 32],
    [128, 256, 24, 32]
  ];
  for (const [logicalProcessors, memoryGb, recommendedWorkers, manualMaxWorkers] of tiers) {
    const profile = computePerformanceProfile({ logicalProcessors, totalMemoryBytes: memoryGb * GIB });
    assert.equal(profile.recommendedWorkers, recommendedWorkers);
    assert.equal(profile.manualMaxWorkers, manualMaxWorkers);
  }
});

test("reported memory rounds to the nearest installed-memory tier", () => {
  const profile = computePerformanceProfile({ logicalProcessors: 24, totalMemoryBytes: 31.9 * GIB });
  assert.equal(profile.memoryGb, 32);
  assert.equal(profile.recommendedWorkers, 12);
  assert.equal(profile.manualMaxWorkers, 16);
});

test("invalid hardware data falls back safely", () => {
  const profile = computePerformanceProfile({ logicalProcessors: 0, totalMemoryBytes: Number.NaN });
  assert.deepEqual(
    { recommended: profile.recommendedWorkers, maximum: profile.manualMaxWorkers },
    { recommended: 1, maximum: 1 }
  );
});

test("detection prefers available parallelism and falls back to the CPU list", () => {
  const preferred = detectPerformanceProfile({
    availableParallelism: () => 12,
    cpus: () => Array.from({ length: 24 }),
    totalmem: () => 32 * GIB
  }, {});
  assert.equal(preferred.logicalProcessors, 12);

  const fallback = detectPerformanceProfile({
    availableParallelism: () => { throw new Error("unavailable"); },
    cpus: () => Array.from({ length: 6 }),
    totalmem: () => 16 * GIB
  }, {});
  assert.equal(fallback.logicalProcessors, 6);
});

test("container memory limits reduce the host-derived worker profile", () => {
  const profile = detectPerformanceProfile({
    availableParallelism: () => 24,
    totalmem: () => 64 * GIB
  }, {
    constrainedMemory: () => 8 * GIB
  });
  assert.equal(profile.memoryGb, 8);
  assert.equal(profile.recommendedWorkers, 3);
  assert.equal(profile.manualMaxWorkers, 4);
});

test("requested workers are clamped to the detected machine maximum", () => {
  const profile = computePerformanceProfile({ logicalProcessors: 24, totalMemoryBytes: 32 * GIB });
  assert.equal(clampRequestedWorkers(undefined, profile), 12);
  assert.equal(clampRequestedWorkers(8, profile), 8);
  assert.equal(clampRequestedWorkers(99, profile), 16);
});

test("memory pressure uses separate pause and resume thresholds", () => {
  const critical = computeMemoryStatus({ totalMemoryBytes: 32 * GIB, freeMemoryBytes: 2 * GIB });
  assert.equal(critical.critical, true);
  assert.equal(critical.canResume, false);
  const between = computeMemoryStatus({ totalMemoryBytes: 32 * GIB, freeMemoryBytes: 3 * GIB });
  assert.equal(between.critical, false);
  assert.equal(between.canResume, false);
  const resumed = computeMemoryStatus({ totalMemoryBytes: 32 * GIB, freeMemoryBytes: 4 * GIB });
  assert.equal(resumed.canResume, true);
});

test("available-memory telemetry wins over low free memory", () => {
  const status = detectMemoryStatus({
    totalmem: () => 16 * GIB,
    freemem: () => 0.5 * GIB
  }, {
    availableMemory: () => 6 * GIB
  }, { platform: "win32" });
  assert.equal(status.freeMemoryBytes, 6 * GIB);
  assert.equal(status.critical, false);
  assert.equal(status.canResume, true);
});

test("Linux uses MemAvailable instead of low non-reclaimable free memory", () => {
  const status = detectMemoryStatus({
    totalmem: () => 16 * GIB,
    freemem: () => 0.25 * GIB
  }, {
    availableMemory: () => 0.25 * GIB,
    constrainedMemory: () => 0
  }, {
    platform: "linux",
    fsApi: { readFileSync: () => "MemTotal:       16777216 kB\nMemAvailable:    6291456 kB\n" }
  });
  assert.equal(status.freeMemoryBytes, 6 * GIB);
  assert.equal(status.critical, false);
  assert.equal(status.canResume, true);
});

test("tiny-memory thresholds preserve a strict hysteresis invariant", () => {
  const status = computeMemoryStatus({ totalMemoryBytes: 1 * GIB, freeMemoryBytes: 0.5 * GIB });
  assert.ok(status.resumeThresholdBytes > status.criticalThresholdBytes);
  assert.equal(status.critical && status.canResume, false);
});
