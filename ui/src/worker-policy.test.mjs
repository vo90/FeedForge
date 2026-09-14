import test from "node:test";
import assert from "node:assert/strict";
import {
  AUTO_SETTING,
  clampWorkerSetting,
  manualWorkerOptions,
  normalizePerformanceProfile,
  resolveConversionWorkerCount,
  resolveInspectionWorkerCount,
  resolveMultiSongWorkerCount,
  resolveSelectedWorkerCount
} from "./worker-policy.mjs";

const profile = {
  logicalProcessors: 24,
  memoryGb: 32,
  recommendedWorkers: 12,
  manualMaxWorkers: 16
};

test("auto resolves to the detected recommendation", () => {
  assert.equal(resolveSelectedWorkerCount(AUTO_SETTING, profile), 12);
});

test("saved manual settings clamp to the current machine", () => {
  assert.equal(clampWorkerSetting(32, profile), 16);
  assert.equal(resolveSelectedWorkerCount(32, profile), 16);
  assert.equal(clampWorkerSetting(AUTO_SETTING, profile), AUTO_SETTING);
});

test("manual options extend through the detected maximum", () => {
  const options = manualWorkerOptions(profile);
  assert.equal(options.length, 16);
  assert.deepEqual(options.slice(-3), [14, 15, 16]);
});

test("stem splitting caps both auto and manual conversion concurrency", () => {
  assert.equal(resolveConversionWorkerCount(1, { separateStems: true, stemJobs: 1 }), 1);
  assert.equal(resolveConversionWorkerCount(12, { separateStems: true, stemJobs: 1 }), 2);
  assert.equal(resolveConversionWorkerCount(16, { separateStems: true, stemJobs: 2 }), 3);
  assert.equal(resolveConversionWorkerCount(16, { separateStems: true, stemJobs: 8 }), 4);
  assert.equal(resolveConversionWorkerCount(16, { separateStems: false, stemJobs: 1 }), 16);
});

test("inspection concurrency scales conservatively with the worker selection", () => {
  assert.equal(resolveInspectionWorkerCount(1), 1);
  assert.equal(resolveInspectionWorkerCount(4), 2);
  assert.equal(resolveInspectionWorkerCount(12), 6);
  assert.equal(resolveInspectionWorkerCount(32), 8);
});

test("multi-song workers use a machine-derived share of the global budget", () => {
  assert.equal(resolveMultiSongWorkerCount(12, 143, profile), 6);
  assert.equal(resolveMultiSongWorkerCount(16, 143, profile), 6);
  assert.equal(resolveMultiSongWorkerCount(12, 3, profile), 3);
  assert.equal(resolveMultiSongWorkerCount(12, 1, profile), 1);
  assert.equal(resolveMultiSongWorkerCount(12, 143, profile, { separateStems: true }), 1);
});

test("multi-song workers scale down and up with the host profile", () => {
  assert.equal(resolveMultiSongWorkerCount(8, 100, {
    logicalProcessors: 8,
    memoryGb: 8,
    recommendedWorkers: 2,
    manualMaxWorkers: 4
  }), 2);
  assert.equal(resolveMultiSongWorkerCount(32, 100, {
    logicalProcessors: 64,
    memoryGb: 128,
    recommendedWorkers: 24,
    manualMaxWorkers: 32
  }), 16);
});

test("malformed profiles normalize to a safe fallback", () => {
  const normalized = normalizePerformanceProfile({ logicalProcessors: 0, memoryGb: 0 });
  assert.deepEqual(normalized, {
    logicalProcessors: 4,
    memoryGb: 8,
    recommendedWorkers: 2,
    manualMaxWorkers: 4
  });
});
