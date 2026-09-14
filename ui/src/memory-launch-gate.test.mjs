import test from "node:test";
import assert from "node:assert/strict";
import { createMemoryLaunchGate } from "./memory-launch-gate.mjs";

test("healthy memory allows a launch immediately", async () => {
  let checks = 0;
  const gate = createMemoryLaunchGate({
    getMemoryStatus: async () => {
      checks += 1;
      return { critical: false, canResume: true };
    }
  });
  assert.equal(await gate(), true);
  assert.equal(checks, 1);
});

test("critical memory pauses until the higher resume threshold is reached", async () => {
  const statuses = [
    { critical: true, canResume: false, freeMemoryBytes: 1 },
    { critical: false, canResume: false, freeMemoryBytes: 2 },
    { critical: false, canResume: true, freeMemoryBytes: 3 }
  ];
  const pauses = [];
  let resumes = 0;
  const gate = createMemoryLaunchGate({
    getMemoryStatus: async () => statuses.shift(),
    onPause: (status) => pauses.push(status.freeMemoryBytes),
    onResume: () => { resumes += 1; },
    wait: async () => {}
  });
  assert.equal(await gate(), true);
  assert.deepEqual(pauses, [1, 2]);
  assert.equal(resumes, 1);
});

test("simultaneous workers share one memory check", async () => {
  let release;
  const status = new Promise((resolve) => { release = resolve; });
  let checks = 0;
  const gate = createMemoryLaunchGate({
    getMemoryStatus: async () => {
      checks += 1;
      return status;
    }
  });
  const first = gate();
  const second = gate();
  assert.equal(first, second);
  release({ critical: false, canResume: true });
  assert.equal(await first, true);
  assert.equal(checks, 1);
});

test("stop cancels a paused launch and telemetry failures fail open", async () => {
  let stopped = false;
  let resumed = 0;
  const stoppedGate = createMemoryLaunchGate({
    getMemoryStatus: async () => ({ critical: true, canResume: false }),
    shouldStop: () => stopped,
    onResume: () => { resumed += 1; },
    wait: async () => { stopped = true; }
  });
  assert.equal(await stoppedGate(), false);
  assert.equal(resumed, 1);

  const failedGate = createMemoryLaunchGate({
    getMemoryStatus: async () => { throw new Error("IPC unavailable"); }
  });
  assert.equal(await failedGate(), true);
});
