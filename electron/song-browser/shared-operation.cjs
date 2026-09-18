'use strict';

// Stop one caller waiting without cancelling cached work used by other callers.
// Keep observing the shared promise after cancellation, including late errors.
function waitForSharedOperation(operation, signal) {
  let onAbort;
  const waiting = new Promise((resolve, reject) => {
    onAbort = () => reject(Object.assign(new Error('Cancelled.'), { code: 'cancelled' }));
    if (signal?.aborted) onAbort();
    else signal?.addEventListener('abort', onAbort, { once: true });
    Promise.resolve(operation).then(resolve, reject);
  });
  return waiting.finally(() => signal?.removeEventListener('abort', onAbort));
}

module.exports = { waitForSharedOperation };
