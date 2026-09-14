export function createMemoryLaunchGate({
  getMemoryStatus,
  shouldStop = () => false,
  onPause = () => {},
  onResume = () => {},
  wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)),
  pollIntervalMs = 750
} = {}) {
  let activeGate = null;

  return function waitForMemoryHeadroom() {
    if (shouldStop()) return Promise.resolve(false);
    if (typeof getMemoryStatus !== "function") return Promise.resolve(true);
    if (activeGate) return activeGate;

    let gate;
    gate = (async () => {
      let paused = false;
      try {
        while (!shouldStop()) {
          const status = await getMemoryStatus();
          if (shouldStop()) break;
          if ((!paused && !status?.critical) || (paused && status?.canResume)) {
            if (paused) onResume(status);
            return true;
          }
          paused = true;
          onPause(status || {});
          await wait(pollIntervalMs);
        }
        if (paused) onResume(null);
        return false;
      } catch {
        // Memory telemetry is a safety aid; an IPC failure must not deadlock work.
        if (paused) onResume(null);
        return true;
      }
    })().finally(() => {
      if (activeGate === gate) activeGate = null;
    });
    activeGate = gate;
    return gate;
  };
}
