"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { createConcurrencyLimiter } = require("../../electron/concurrency-limiter.cjs");

test("the limiter never admits more than the detected maximum", async () => {
  const limiter = createConcurrencyLimiter(4);
  let active = 0;
  let maximumActive = 0;
  await Promise.all(Array.from({ length: 100 }, (_unused, index) => limiter.run(async () => {
    active += 1;
    maximumActive = Math.max(maximumActive, active);
    await Promise.resolve();
    active -= 1;
    return index;
  })));
  assert.equal(maximumActive, 4);
  assert.equal(limiter.activeCount, 0);
  assert.equal(limiter.waitingCount, 0);
});

test("failed work releases its admission slot", async () => {
  const limiter = createConcurrencyLimiter(1);
  await assert.rejects(limiter.run(async () => { throw new Error("failed"); }), /failed/);
  assert.equal(await limiter.run(async () => "next"), "next");
});

test("weighted work reserves its full worker width", async () => {
  const limiter = createConcurrencyLimiter(8);
  let releasePlan;
  const plan = limiter.run(
    () => new Promise((resolve) => { releasePlan = resolve; }),
    { weight: 6 }
  );
  await Promise.resolve();
  assert.equal(limiter.activeCount, 6);
  const conversions = [1, 2, 3].map((value) => limiter.run(async () => value));
  await Promise.resolve();
  assert.equal(limiter.activeCount, 8);
  assert.equal(limiter.waitingCount, 1);
  releasePlan("planned");
  assert.equal(await plan, "planned");
  assert.deepEqual(await Promise.all(conversions), [1, 2, 3]);
});

test("an aborted waiter is removed before it can launch", async () => {
  const limiter = createConcurrencyLimiter(1);
  let releaseActive;
  const active = limiter.run(() => new Promise((resolve) => { releaseActive = resolve; }));
  await Promise.resolve();
  const controller = new AbortController();
  const waiting = limiter.run(async () => "must not run", { signal: controller.signal });
  assert.equal(limiter.waitingCount, 1);
  controller.abort();
  await assert.rejects(waiting, { name: "AbortError" });
  assert.equal(limiter.waitingCount, 0);
  releaseActive();
  await active;
});
