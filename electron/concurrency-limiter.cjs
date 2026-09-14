"use strict";

function createConcurrencyLimiter(value) {
  const limit = Math.max(1, Math.floor(Number(value) || 1));
  let activeCount = 0;
  const waiting = [];

  function abortError() {
    const error = new Error("Operation cancelled while waiting for converter capacity.");
    error.name = "AbortError";
    return error;
  }

  function dispatch() {
    while (waiting.length) {
      const entry = waiting[0];
      if (activeCount + entry.weight > limit) return;
      waiting.shift();
      if (entry.signal && entry.onAbort) {
        entry.signal.removeEventListener("abort", entry.onAbort);
      }
      activeCount += entry.weight;
      let released = false;
      entry.resolve(() => {
        if (released) return;
        released = true;
        activeCount -= entry.weight;
        dispatch();
      });
    }
  }

  function acquire({ weight: requestedWeight = 1, signal = null } = {}) {
    const weight = Math.max(1, Math.min(limit, Math.floor(Number(requestedWeight) || 1)));
    if (signal?.aborted) return Promise.reject(abortError());
    return new Promise((resolve, reject) => {
      const entry = { weight, signal, resolve, reject, onAbort: null };
      if (signal) {
        entry.onAbort = () => {
          const index = waiting.indexOf(entry);
          if (index < 0) return;
          waiting.splice(index, 1);
          reject(abortError());
          dispatch();
        };
        signal.addEventListener("abort", entry.onAbort, { once: true });
      }
      waiting.push(entry);
      dispatch();
    });
  }

  async function run(task, options = {}) {
    if (typeof task !== "function") throw new TypeError("Concurrency limiter requires a task function.");
    const release = await acquire(options);
    try {
      return await task();
    } finally {
      release();
    }
  }

  return {
    limit,
    run,
    get activeCount() { return activeCount; },
    get waitingCount() { return waiting.length; }
  };
}

module.exports = { createConcurrencyLimiter };
