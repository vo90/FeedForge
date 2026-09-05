// Keep the user's search while Find songs is unmounted by another section.
// Sessions stay in memory for this renderer/API only; nothing is persisted and
// creating or subscribing to a session never requests a website page.
const sessions = new WeakMap();

function message(error) {
  const value = error?.message || error?.error || error;
  return (typeof value === 'string' ? value : 'The song search failed. Please try again.').slice(0, 1200);
}

function createSession(api) {
  let state = { query: '', searchedQuery: '', result: null, pending: false, error: '' };
  let inFlight = null;
  const listeners = new Set();
  const update = (next) => {
    state = { ...state, ...next };
    for (const listener of listeners) {
      try { listener(); } catch { /* A departing view cannot interrupt a search. */ }
    }
  };
  return {
    getSnapshot: () => state,
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
    setQuery(query) { if (typeof query === 'string' && query !== state.query) update({ query }); },
    search(query, page = 1) {
      if (inFlight) return inFlight;
      const trimmed = typeof query === 'string' ? query.trim() : '';
      if (trimmed.length < 2 || trimmed.length > 160 || !Number.isInteger(page) || page < 1 || page > 10000) {
        update({ error: 'Enter between 2 and 160 characters and choose a valid search page.' });
        return Promise.resolve(null);
      }
      if (typeof api?.search !== 'function') {
        update({ error: 'Song search is available in the FeedForge desktop app.' });
        return Promise.resolve(null);
      }
      // Install the shared promise before notifying subscribers. A second view
      // or a fast repeat submission joins this request instead of starting one.
      inFlight = Promise.resolve().then(() => api.search({ query: trimmed, page })).then((result) => {
        if (result?.ok === false) throw new Error(message(result.error));
        if (!result) throw new Error('The song search returned no response. Please try again.');
        update({ result: { ...result, page: result.page ?? page }, error: '' });
        return result;
      }).catch((error) => {
        update({ result: null, error: message(error) });
        return null;
      }).finally(() => {
        inFlight = null;
        update({ pending: false });
      });
      update({ pending: true, error: '', searchedQuery: trimmed });
      return inFlight;
    },
  };
}

export function getSearchSession(api) {
  if (!api || !['object', 'function'].includes(typeof api)) return createSession(null);
  if (!sessions.has(api)) sessions.set(api, createSession(api));
  return sessions.get(api);
}
