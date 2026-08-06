const assert = require("node:assert/strict");
const test = require("node:test");

async function scheduler() {
  return import("../ui/src/conversion-scheduler.mjs");
}

test("uses the selected worker count when no linked RS1 archives are queued", async () => {
  const { runConversionQueues } = await scheduler();
  let active = 0;
  let maximumActive = 0;
  const converted = [];

  await runConversionQueues({
    regularItems: Array.from({ length: 18 }, (_value, index) => index),
    workerLimit: 6,
    runItem: async (item) => {
      active += 1;
      maximumActive = Math.max(maximumActive, active);
      await new Promise((resolve) => setImmediate(resolve));
      converted.push(item);
      active -= 1;
    }
  });

  assert.equal(maximumActive, 6);
  assert.deepEqual(converted.toSorted((left, right) => left - right), Array.from({ length: 18 }, (_value, index) => index));
});

test("RS1 worker joins regular conversions after linked archives finish", async () => {
  const { runConversionQueues } = await scheduler();
  const linkedItems = ["rs1-disc", "rs1-dlc", "rs1-songs"];
  const regularItems = Array.from({ length: 6 }, (_value, index) => `regular-${index}`);
  const converted = [];
  let activeLinked = 0;
  let maximumLinked = 0;
  let activeRegular = 0;
  let maximumRegular = 0;
  let releaseRegular;
  const regularGate = new Promise((resolve) => {
    releaseRegular = resolve;
  });

  await runConversionQueues({
    linkedItems,
    regularItems,
    workerLimit: 6,
    runItem: async (item) => {
      if (item.startsWith("rs1-")) {
        activeLinked += 1;
        maximumLinked = Math.max(maximumLinked, activeLinked);
        await new Promise((resolve) => setImmediate(resolve));
        activeLinked -= 1;
      } else {
        activeRegular += 1;
        maximumRegular = Math.max(maximumRegular, activeRegular);
        if (activeRegular === 6) releaseRegular();
        await regularGate;
        activeRegular -= 1;
      }
      converted.push(item);
    }
  });

  assert.equal(maximumLinked, 1);
  assert.equal(maximumRegular, 6);
  assert.deepEqual(new Set(converted), new Set([...linkedItems, ...regularItems]));
});

test("keeps linked RS1 conversions serialized when no regular work exists", async () => {
  const { runConversionQueues } = await scheduler();
  let active = 0;
  let maximumActive = 0;

  await runConversionQueues({
    linkedItems: ["disc", "dlc", "songs"],
    workerLimit: 8,
    runItem: async () => {
      active += 1;
      maximumActive = Math.max(maximumActive, active);
      await new Promise((resolve) => setImmediate(resolve));
      active -= 1;
    }
  });

  assert.equal(maximumActive, 1);
});
