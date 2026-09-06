// Keep the user's search while Find songs is unmounted by another section.
// Sessions stay in memory for this renderer/API only; nothing is persisted and
// creating or subscribing to a session never requests a website page.
const sessions = new WeakMap();
const sortFields = ['title', 'artist', 'album', 'tuning', 'creator', 'added', 'updated', 'year', 'duration', 'downloads'];
const allParts = ['lead', 'rhythm', 'bass'];
const defaultFilters = () => ({ exactArtist: '', parts: [], tuning: '', creator: '', hideReported: false, hideAbandoned: false, availableOnly: false, hideConverted: false });

function message(error) {
  const value = error?.message || error?.error || error;
  return (typeof value === 'string' ? value : 'The song search failed. Please try again.').slice(0, 1200);
}

function normalizeSort(value) {
  if (!value || typeof value !== 'object' || !sortFields.includes(value.field) || !['asc', 'desc'].includes(value.direction)) throw new Error('Choose a valid search order.');
  return { field: value.field, direction: value.direction };
}

function normalizeFilters(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Choose valid search filters.');
  const filters = defaultFilters();
  for (const key of ['exactArtist', 'tuning', 'creator']) {
    const text = value[key] ?? '';
    if (typeof text !== 'string' || text.length > 160 || /[\u0000-\u001f\u007f]/.test(text)) throw new Error('Choose valid search filters.');
    filters[key] = text.replace(/\s+/g, ' ').trim();
  }
  const parts = value.parts ?? [];
  if (!Array.isArray(parts) || parts.length > 3 || parts.some((part) => !allParts.includes(part))) throw new Error('Choose lead, rhythm or bass arrangements.');
  filters.parts = allParts.filter((part) => parts.includes(part));
  for (const key of ['hideReported', 'hideAbandoned', 'availableOnly', 'hideConverted']) {
    if (value[key] !== undefined && typeof value[key] !== 'boolean') throw new Error('Choose valid search filters.');
    filters[key] = value[key] ?? false;
  }
  return filters;
}

function identity(request, includeSort = true, includePage = false) {
  const fold = (value) => value.toLocaleLowerCase('en-US');
  return JSON.stringify({ query: fold(request.query), ...(includeSort ? { sort: request.sort } : {}), filters: { ...request.filters, exactArtist: fold(request.filters.exactArtist), tuning: fold(request.filters.tuning), creator: fold(request.filters.creator) }, ...(includePage ? { page: request.page } : {}) });
}

function createSession(api) {
  let state = { query: '', searchedQuery: '', sort: { field: 'title', direction: 'asc' }, filters: defaultFilters(), searchedRequest: null, requestIdentity: '', selectionScope: '', result: null, pending: false, error: '', requestId: null, progress: null };
  let inFlight = null;
  let generation = 0;
  let controlsChosen = false;
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
    setProgress(progress) {
      if (!state.pending || !progress || progress.requestId !== state.requestId) return;
      update({ progress: { ...progress } });
    },
    async cancel() {
      const requestId = state.requestId;
      if (!state.pending) return;
      generation++; inFlight = null;
      update({ pending: false, progress: null, requestId: null, error: 'Search cancelled.' });
      if (requestId && typeof api.cancelSearch === 'function') {
        try { await api.cancelSearch({ requestId }); } catch { /* Late cancellation cannot replace a newer search. */ }
      }
    },
    setQuery(query) { if (typeof query === 'string' && query !== state.query) update({ query }); },
    setSort(sort) {
      try { const normalized = normalizeSort(sort); controlsChosen = true; update({ sort: normalized, error: '' }); }
      catch (error) { update({ error: message(error) }); }
    },
    setFilters(filters) {
      try {
        const draft = { ...state.filters, ...filters };
        const normalized = normalizeFilters(draft);
        // Preserve spaces while the user types "Iron Maiden". Canonicalization
        // belongs to submission, not a controlled input's change handler.
        for (const key of ['exactArtist', 'tuning', 'creator']) normalized[key] = draft[key] ?? '';
        controlsChosen = true; update({ filters: normalized, error: '' });
      }
      catch (error) { update({ error: message(error) }); }
    },
    search(input, page = 1) {
      let request, legacy;
      try {
        const objectRequest = input && typeof input === 'object' && !Array.isArray(input);
        const value = objectRequest ? input : { query: input, page };
        const query = typeof value.query === 'string' ? value.query.replace(/\s+/g, ' ').trim() : '';
        const nextPage = value.page ?? 1;
        if (query.length < 2 || query.length > 160 || /[\u0000-\u001f\u007f]/.test(value.query) || !Number.isInteger(nextPage) || nextPage < 1 || nextPage > 10000) throw new Error('Enter between 2 and 160 characters and choose a valid search page.');
        request = { query, page: nextPage, sort: normalizeSort(value.sort ?? state.sort), filters: normalizeFilters(value.filters ?? state.filters) };
        legacy = !objectRequest && !controlsChosen;
        // Changing the search definition starts at page one. Keep legacy direct
        // page calls compatible, including an initial search on page two.
        if (!legacy && state.searchedRequest && identity(request, true) !== identity(state.searchedRequest, true)) request.page = 1;
      } catch (error) {
        generation++;
        inFlight = null;
        update({ pending: false, error: message(error) });
        return Promise.resolve(null);
      }
      if (typeof api?.search !== 'function') {
        update({ error: 'Song search is available in the FeedForge desktop app.' });
        return Promise.resolve(null);
      }
      const key = identity(request, true, true);
      if (inFlight?.key === key) return inFlight.promise;
      if (state.pending && state.requestId && typeof api.cancelSearch === 'function') Promise.resolve(api.cancelSearch({ requestId: state.requestId })).catch(() => {});
      const current = ++generation;
      const requestId = typeof api.cancelSearch === 'function' ? (globalThis.crypto?.randomUUID?.() || `search-${Date.now()}-${current}-${Math.random().toString(36).slice(2)}`) : null;
      const payload = { ...(legacy ? { query: request.query, page: request.page } : request), ...(requestId ? { requestId } : {}) };
      if (!legacy) controlsChosen = true;
      // The main process serializes browser navigation. Only the newest request
      // may publish a result, failure, or pending-state change to this session.
      const promise = Promise.resolve().then(() => api.search(payload)).then((result) => {
        if (result?.ok === false) throw new Error(message(result.error));
        if (!result) throw new Error('The song search returned no response. Please try again.');
        if (current === generation) update({ result: { ...result, page: result.page ?? request.page }, error: '' });
        return result;
      }).catch((error) => {
        if (current === generation) update({ result: null, error: message(error) });
        return null;
      }).finally(() => {
        if (current === generation) {
          inFlight = null;
          update({ pending: false, progress: null });
        }
      });
      inFlight = { key, promise };
      update({ pending: true, result: null, error: '', searchedQuery: request.query, sort: request.sort, filters: request.filters, searchedRequest: request, requestIdentity: identity(request), selectionScope: identity(request, false), requestId, progress: null });
      return promise;
    },
  };
}

export function getSearchSession(api) {
  if (!api || !['object', 'function'].includes(typeof api)) return createSession(null);
  if (!sessions.has(api)) sessions.set(api, createSession(api));
  return sessions.get(api);
}
