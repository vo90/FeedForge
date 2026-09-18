// Only the main process decides reuse suitability. The renderer scopes those
// decisions to the exact visible charts and requirements that requested them.
export function createResultAssessmentSession(api, { delayMs = 120 } = {}) {
  let generation = 0;
  let snapshot = { scope: '', results: {} };
  const listeners = new Set();
  const update = (value) => { snapshot = value; for (const listener of listeners) listener(); };
  return {
    getSnapshot: () => snapshot,
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
    cancel() { generation++; },
    async assess(entries, selection, scope) {
      const current = ++generation;
      const visible = entries.slice(0, 50);
      const results = Object.fromEntries(visible.map((entry) => [entry.id, { reusable: false, pending: true }]));
      update({ scope, results: { ...results } });
      if (delayMs) await new Promise((resolve) => setTimeout(resolve, delayMs));
      for (const entry of visible) {
        if (current !== generation) return;
        let decision;
        try {
          decision = await api.assessResult({ id: entry.id, jobId: entry.jobId, selection });
          if (!decision || decision.ok === false) throw new Error(decision?.error || 'Saved-output suitability could not be verified.');
        } catch (error) {
          decision = { reusable: false, status: 'insufficient_evidence', reason: String(error.message || 'Saved-output suitability could not be verified.').slice(0, 500) };
        }
        if (current !== generation) return;
        results[entry.id] = { ...decision, reusable: decision.reusable === true, pending: false };
        update({ scope, results: { ...results } });
      }
    },
  };
}
