import test from "node:test";
import assert from "node:assert/strict";
import { runConversionQueues, usesSharedRs1SongsAudio } from "./conversion-scheduler.mjs";

function nextTurn() {
  return new Promise((resolve) => setImmediate(resolve));
}

function deferred() {
  let resolve;
  const promise = new Promise((promiseResolve) => { resolve = promiseResolve; });
  return { promise, resolve };
}

async function waitUntil(predicate, message = "Timed out waiting for scheduler state.") {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (predicate()) return;
    await nextTurn();
  }
  assert.fail(message);
}

test("shared RS1 archives are identified without serializing disc compatibility", () => {
  assert.equal(usesSharedRs1SongsAudio("D:\\dlc\\songs.psarc"), true);
  assert.equal(usesSharedRs1SongsAudio("D:\\dlc\\rs1compatibilitydlc_p.psarc"), true);
  assert.equal(usesSharedRs1SongsAudio("D:\\dlc\\rs1compatibilitydisc_p.psarc"), false);
  assert.equal(usesSharedRs1SongsAudio("D:\\dlc\\ordinary.psarc"), false);
});

test("Auto-like budget runs linked weight 6 beside regular weight 6", async () => {
  const release = deferred();
  const started = [];
  let activeWeight = 0;
  let activeLinked = 0;
  let maximumWeight = 0;
  const running = runConversionQueues({
    linkedItems: [{ id: "linked", linked: true, workerWeight: 6 }],
    regularItems: [{ id: "regular", linked: false, workerWeight: 6 }],
    workerLimit: 12,
    runItem: async (item, grantedWeight) => {
      started.push([item.id, grantedWeight]);
      activeWeight += grantedWeight;
      if (item.linked) activeLinked += 1;
      maximumWeight = Math.max(maximumWeight, activeWeight);
      await release.promise;
      activeWeight -= grantedWeight;
      if (item.linked) activeLinked -= 1;
    }
  });
  try {
    await waitUntil(() => started.length === 2);
    assert.deepEqual(started, [["linked", 6], ["regular", 6]]);
    assert.equal(activeWeight, 12);
    assert.equal(activeLinked, 1);
    assert.equal(maximumWeight, 12);
  } finally {
    release.resolve();
    await running;
  }
});

test("manual-like budget fills linked 6, regular 6, and four single slots", async () => {
  const release = deferred();
  const started = [];
  let activeWeight = 0;
  let maximumWeight = 0;
  const running = runConversionQueues({
    linkedItems: [{ id: "linked", workerWeight: 6 }],
    regularItems: [
      { id: "regular-heavy", workerWeight: 6 },
      ...Array.from({ length: 4 }, (_unused, index) => ({ id: `regular-${index}`, workerWeight: 1 }))
    ],
    workerLimit: 16,
    runItem: async (item, grantedWeight) => {
      started.push([item.id, grantedWeight]);
      activeWeight += grantedWeight;
      maximumWeight = Math.max(maximumWeight, activeWeight);
      await release.promise;
      activeWeight -= grantedWeight;
    }
  });
  try {
    await waitUntil(() => started.length === 6);
    assert.deepEqual(started.map(([id]) => id), [
      "linked", "regular-heavy", "regular-0", "regular-1", "regular-2", "regular-3"
    ]);
    assert.deepEqual(started.map(([, weight]) => weight), [6, 6, 1, 1, 1, 1]);
    assert.equal(activeWeight, 16);
    assert.equal(maximumWeight, 16);
  } finally {
    release.resolve();
    await running;
  }
});

test("linked work stays serial while regular work consumes its remaining budget", async () => {
  const linkedItems = Array.from(
    { length: 10 },
    (_unused, index) => ({ id: `linked-${index}`, linked: true, workerWeight: 3 })
  );
  const regularItems = Array.from(
    { length: 30 },
    (_unused, index) => ({ id: `regular-${index}`, linked: false })
  );
  const seen = new Set();
  let activeWeight = 0;
  let activeLinked = 0;
  let maximumWeight = 0;
  let maximumLinked = 0;
  await runConversionQueues({
    linkedItems,
    regularItems,
    workerLimit: 7,
    runItem: async (item, grantedWeight) => {
      assert.equal(seen.has(item.id), false);
      seen.add(item.id);
      activeWeight += grantedWeight;
      if (item.linked) activeLinked += 1;
      maximumWeight = Math.max(maximumWeight, activeWeight);
      maximumLinked = Math.max(maximumLinked, activeLinked);
      assert.ok(activeWeight <= 7);
      await nextTurn();
      activeWeight -= grantedWeight;
      if (item.linked) activeLinked -= 1;
    }
  });
  assert.equal(seen.size, 40);
  assert.equal(maximumLinked, 1);
  assert.equal(maximumWeight, 7);
});

test("a heavy head item reserves capacity instead of being starved by later singles", async () => {
  const releaseBlocker = deferred();
  const releaseHeavy = deferred();
  const started = [];
  const running = runConversionQueues({
    regularItems: [
      { id: "blocker", workerWeight: 1 },
      { id: "heavy", workerWeight: 6 },
      ...Array.from({ length: 20 }, (_unused, index) => ({ id: `light-${index}`, workerWeight: 1 }))
    ],
    workerLimit: 6,
    runItem: async (item) => {
      started.push(item.id);
      if (item.id === "blocker") await releaseBlocker.promise;
      if (item.id === "heavy") await releaseHeavy.promise;
      await nextTurn();
    }
  });

  let whileBlocked;
  let whileHeavy;
  try {
    await waitUntil(() => started.includes("blocker"));
    await nextTurn();
    whileBlocked = [...started];
    releaseBlocker.resolve();
    await waitUntil(() => started.includes("heavy"));
    await nextTurn();
    whileHeavy = [...started];
  } finally {
    releaseBlocker.resolve();
    releaseHeavy.resolve();
    await running;
  }

  assert.deepEqual(whileBlocked, ["blocker"]);
  assert.deepEqual(whileHeavy, ["blocker", "heavy"]);
  assert.equal(started.length, 22);
});

test("requested weights are normalized and clamped to the global limit", async () => {
  const granted = new Map();
  let activeWeight = 0;
  let maximumWeight = 0;
  await runConversionQueues({
    regularItems: [
      { id: "huge", workerWeight: 99 },
      { id: "default" },
      { id: "fraction", workerWeight: 2.9 },
      { id: "zero", workerWeight: 0 }
    ],
    workerLimit: 6,
    runItem: async (item, grantedWeight) => {
      granted.set(item.id, grantedWeight);
      activeWeight += grantedWeight;
      maximumWeight = Math.max(maximumWeight, activeWeight);
      assert.ok(activeWeight <= 6);
      await nextTurn();
      activeWeight -= grantedWeight;
    }
  });
  assert.deepEqual(Object.fromEntries(granted), {
    huge: 6,
    default: 1,
    fraction: 2,
    zero: 1
  });
  assert.equal(maximumWeight, 6);
});

test("large queues run every item once without exceeding the selected limit", async () => {
  const items = Array.from({ length: 4000 }, (_unused, index) => index);
  const seen = new Set();
  let activeWeight = 0;
  let maximumWeight = 0;
  await runConversionQueues({
    regularItems: items,
    workerLimit: 32,
    runItem: async (item, grantedWeight) => {
      assert.equal(grantedWeight, 1);
      activeWeight += grantedWeight;
      maximumWeight = Math.max(maximumWeight, activeWeight);
      assert.ok(activeWeight <= 32);
      assert.equal(seen.has(item), false);
      seen.add(item);
      await Promise.resolve();
      activeWeight -= grantedWeight;
    }
  });
  assert.equal(seen.size, 4000);
  assert.equal(maximumWeight, 32);
});

test("the launch gate pauses new work and can cancel waiting workers", async () => {
  let releaseGate;
  const gate = new Promise((resolve) => { releaseGate = resolve; });
  let runs = 0;
  const running = runConversionQueues({
    regularItems: [1, 2, 3, 4],
    workerLimit: 4,
    beforeStart: async () => {
      await gate;
      return false;
    },
    runItem: async () => { runs += 1; }
  });
  await nextTurn();
  assert.equal(runs, 0);
  releaseGate();
  await running;
  assert.equal(runs, 0);
});

test("stop is rechecked after an asynchronous launch gate", async () => {
  const gate = deferred();
  let stopped = false;
  let gateCalls = 0;
  let runs = 0;
  const running = runConversionQueues({
    regularItems: [1, 2, 3, 4],
    workerLimit: 4,
    shouldStop: () => stopped,
    beforeStart: async () => {
      gateCalls += 1;
      await gate.promise;
      return true;
    },
    runItem: async () => { runs += 1; }
  });
  try {
    await waitUntil(() => gateCalls === 1);
    stopped = true;
  } finally {
    gate.resolve();
    await running;
  }
  assert.equal(gateCalls, 1);
  assert.equal(runs, 0);
});

test("stop prevents refilling capacity after already-running items finish", async () => {
  const release = deferred();
  const started = [];
  let stopped = false;
  const running = runConversionQueues({
    regularItems: Array.from({ length: 20 }, (_unused, index) => index),
    workerLimit: 4,
    shouldStop: () => stopped,
    runItem: async (item) => {
      started.push(item);
      await release.promise;
    }
  });
  try {
    await waitUntil(() => started.length === 4);
    stopped = true;
  } finally {
    release.resolve();
    await running;
  }
  assert.deepEqual(started, [0, 1, 2, 3]);
});

test("the launch gate is not called after the queue is exhausted", async () => {
  let gateCalls = 0;
  let runs = 0;
  await runConversionQueues({
    regularItems: [1],
    workerLimit: 8,
    beforeStart: async () => {
      gateCalls += 1;
      return true;
    },
    runItem: async () => { runs += 1; }
  });
  assert.equal(runs, 1);
  assert.equal(gateCalls, 1);
});
