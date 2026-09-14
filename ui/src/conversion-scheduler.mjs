function normalizedWorkerLimit(value) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return 1;
  return Math.max(1, Math.floor(parsed));
}

function normalizedWorkerWeight(item, limit) {
  const parsed = Number(item?.workerWeight ?? 1);
  if (!Number.isFinite(parsed)) return 1;
  return Math.min(limit, Math.max(1, Math.floor(parsed)));
}

function archiveName(filePath) {
  return String(filePath || "").replace(/\\/g, "/").split("/").pop().toLowerCase();
}

/** Return true only for RS1 archives that share the large songs.psarc payload. */
export function usesSharedRs1SongsAudio(filePath) {
  const name = archiveName(filePath);
  if (name === "songs.psarc") return true;
  if (!name.endsWith(".psarc") || !name.includes("rs1compatibility")) return false;
  return !/^rs1compatibilitydisc(?:[_\-.]|$)/.test(name);
}

/**
 * Run conversions within one global weighted budget while keeping linked RS1
 * archives serialized. A head item that cannot yet fit reserves the next
 * opening, preventing lighter work behind it from delaying it indefinitely.
 */
export async function runConversionQueues({
  linkedItems = [],
  regularItems = [],
  workerLimit = 1,
  runItem,
  shouldStop = () => false,
  beforeStart = null
}) {
  if (typeof runItem !== "function") {
    throw new TypeError("runConversionQueues requires a runItem function.");
  }

  const linkedQueue = Array.isArray(linkedItems) ? linkedItems : [];
  const regularQueue = Array.isArray(regularItems) ? regularItems : [];
  const limit = normalizedWorkerLimit(workerLimit);
  if (!linkedQueue.length && !regularQueue.length) return;
  let linkedIndex = 0;
  let regularIndex = 0;
  let activeWeight = 0;
  let activeLinked = 0;
  let reservedQueue = null;
  let launchClosed = false;
  let hasFailure = false;
  let firstFailure;
  const running = new Set();

  const rememberFailure = (error) => {
    if (!hasFailure) {
      hasFailure = true;
      firstFailure = error;
    }
    launchClosed = true;
  };

  const headItem = (queueName) => {
    const linked = queueName === "linked";
    const queue = linked ? linkedQueue : regularQueue;
    const index = linked ? linkedIndex : regularIndex;
    if (index >= queue.length) return null;
    const item = queue[index];
    return {
      queueName,
      item,
      grantedWeight: normalizedWorkerWeight(item, limit)
    };
  };

  const candidateToStart = () => {
    const availableWeight = limit - activeWeight;

    if (reservedQueue) {
      const reserved = headItem(reservedQueue);
      if (!reserved) {
        reservedQueue = null;
      } else if (
        (reserved.queueName !== "linked" || activeLinked === 0)
        && reserved.grantedWeight <= availableWeight
      ) {
        return reserved;
      } else {
        return null;
      }
    }

    if (activeLinked === 0) {
      const linked = headItem("linked");
      if (linked) {
        if (linked.grantedWeight <= availableWeight) return linked;
        reservedQueue = "linked";
        return null;
      }
    }

    const regular = headItem("regular");
    if (!regular) return null;
    if (regular.grantedWeight <= availableWeight) return regular;
    reservedQueue = "regular";
    return null;
  };

  const startCandidate = (candidate) => {
    if (candidate.queueName === "linked") {
      linkedIndex += 1;
      activeLinked += 1;
    } else {
      regularIndex += 1;
    }
    if (reservedQueue === candidate.queueName) reservedQueue = null;
    activeWeight += candidate.grantedWeight;

    let task;
    task = Promise.resolve()
      .then(() => runItem(candidate.item, candidate.grantedWeight))
      .catch(rememberFailure)
      .finally(() => {
        activeWeight -= candidate.grantedWeight;
        if (candidate.queueName === "linked") activeLinked -= 1;
        running.delete(task);
      });
    running.add(task);
  };

  while (true) {
    if (!launchClosed && shouldStop()) launchClosed = true;

    let candidate = launchClosed ? null : candidateToStart();
    if (candidate) {
      if (beforeStart) {
        try {
          if (await beforeStart() === false) {
            launchClosed = true;
            continue;
          }
        } catch (error) {
          rememberFailure(error);
          continue;
        }
        if (launchClosed || shouldStop()) {
          launchClosed = true;
          continue;
        }
        // Active work may have completed while the gate was pending. Reapply
        // queue priority and reservations before claiming exactly one item.
        candidate = candidateToStart();
        if (!candidate) continue;
      }
      startCandidate(candidate);
      continue;
    }

    if (running.size === 0) {
      if (hasFailure) throw firstFailure;
      return;
    }
    await Promise.race(running);
  }
}
