'use strict';

// Catalogue contracts contain public chart metadata only. They do not confer
// download authority and never accept or generate provider download URLs.
const SORT_FIELDS = Object.freeze(['title', 'artist', 'album', 'tuning', 'creator', 'added', 'updated', 'year', 'duration', 'downloads']);
const PARTS = Object.freeze(['lead', 'rhythm', 'bass']);
const EMPTY_FILTERS = Object.freeze({ exactArtist: '', parts: Object.freeze([]), tuning: '', creator: '', hideReported: false, hideAbandoned: false, availableOnly: false, hideConverted: false });
const compact = (value) => String(value ?? '').replace(/\s+/g, ' ').trim();
const comparable = (value) => compact(value).toLocaleLowerCase('en-US');

function boundedText(value, name, max = 160) {
  if (value === undefined || value === null) return '';
  if (typeof value !== 'string' || value.length > max || /[\u0000-\u001f\u007f]/.test(value)) throw new TypeError(`Choose a valid ${name}.`);
  return compact(value);
}

function normalizeSearchRequest(input) {
  const value = typeof input === 'string' ? { query: input } : input;
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new TypeError('Enter a song or artist to search.');
  const query = boundedText(value.query, 'search query');
  if (query.length < 2) throw new TypeError('Enter between 2 and 160 characters to search.');
  const page = value.page === undefined ? 1 : value.page;
  if (!Number.isInteger(page) || page < 1 || page > 10000) throw new TypeError('Choose a valid search page.');
  const sortValue = value.sort === undefined ? {} : value.sort;
  if (!sortValue || typeof sortValue !== 'object' || Array.isArray(sortValue)) throw new TypeError('Choose a valid search order.');
  const field = sortValue.field ?? 'title';
  const direction = sortValue.direction ?? 'asc';
  if (!SORT_FIELDS.includes(field) || !['asc', 'desc'].includes(direction)) throw new TypeError('Choose a valid search order.');
  const filterValue = value.filters === undefined ? {} : value.filters;
  if (!filterValue || typeof filterValue !== 'object' || Array.isArray(filterValue)) throw new TypeError('Choose valid search filters.');
  const parts = filterValue.parts ?? [];
  if (!Array.isArray(parts) || parts.length > 3 || parts.some((part) => !PARTS.includes(part))) throw new TypeError('Choose lead, rhythm or bass arrangements.');
  const flags = {};
  for (const name of ['hideReported', 'hideAbandoned', 'availableOnly', 'hideConverted']) {
    if (filterValue[name] !== undefined && typeof filterValue[name] !== 'boolean') throw new TypeError('Choose valid search filters.');
    flags[name] = filterValue[name] ?? false;
  }
  return {
    query, page, sort: { field, direction }, filters: {
      exactArtist: boundedText(filterValue.exactArtist, 'artist filter'),
      parts: PARTS.filter((part) => parts.includes(part)),
      tuning: boundedText(filterValue.tuning, 'tuning filter'),
      creator: boundedText(filterValue.creator, 'creator filter'),
      ...flags,
    },
  };
}

function searchIdentity(input, { includePage = false, includeSort = true } = {}) {
  const request = normalizeSearchRequest(input);
  return JSON.stringify({
    query: comparable(request.query),
    ...(includeSort ? { sort: request.sort } : {}),
    filters: {
      ...request.filters,
      exactArtist: comparable(request.filters.exactArtist),
      tuning: comparable(request.filters.tuning),
      creator: comparable(request.filters.creator),
    },
    ...(includePage ? { page: request.page } : {}),
  });
}

function hasLocalFilters(filters) {
  return !!(filters?.exactArtist || filters?.parts?.length || filters?.tuning || filters?.creator || filters?.hideReported || filters?.hideAbandoned || filters?.availableOnly || filters?.hideConverted);
}

function parseParts(value) {
  if (Array.isArray(value)) return PARTS.filter((part) => value.some((item) => comparable(item) === part));
  const label = compact(value);
  return PARTS.filter((part) => new RegExp('\\b' + part + '\\b', 'i').test(label));
}

function chartFilterDecision(chart, filters = EMPTY_FILTERS) {
  const unknown = [];
  const excluded = [];
  for (const [filter, field] of [['exactArtist', 'artist'], ['creator', 'creator']]) {
    if (!filters[filter]) continue;
    if (!compact(chart?.[field])) unknown.push(field);
    else if (comparable(chart[field]) !== comparable(filters[filter])) excluded.push(field);
  }
  if (filters.parts?.length) {
    const parts = parseParts(chart?.arrangements?.length ? chart.arrangements : chart?.parts);
    if (!parts.length) unknown.push('parts');
    else if (!filters.parts.every((part) => parts.includes(part))) excluded.push('parts');
  }
  if (filters.tuning) {
    // A label saying "Multiple tunings" cannot establish arrangement-specific
    // tuning. It is not a match even if another part happens to have that tuning.
    const tuning = compact(chart?.tuning);
    if (!tuning || /\b(?:multiple|various|unknown|custom)\b/i.test(tuning)) unknown.push('tuning');
    else if (comparable(tuning) !== comparable(filters.tuning)) excluded.push('tuning');
  }
  if (filters.hideReported && chart?.reported === true) excluded.push('reported');
  if (filters.hideAbandoned && chart?.abandoned === true) excluded.push('abandoned');
  if (filters.hideConverted && chart?.alreadyConverted === true) excluded.push('alreadyConverted');
  if (filters.availableOnly && chart?.supported !== true) {
    if (typeof chart?.supported !== 'boolean') unknown.push('supported');
    else excluded.push('supported');
  }
  return { matches: !excluded.length && !unknown.length, unknown, excluded };
}

function chartMatchesFilters(chart, filters) { return chartFilterDecision(chart, filters).matches; }

function filterCharts(charts, filters = EMPTY_FILTERS) {
  if (!Array.isArray(charts)) throw new TypeError('The collected search must contain chart records.');
  return charts.filter((chart) => chartMatchesFilters(chart, filters));
}

function sortCharts(charts, sort = { field: 'title', direction: 'asc' }) {
  if (!SORT_FIELDS.includes(sort?.field) || !['asc', 'desc'].includes(sort?.direction)) throw new TypeError('Choose a valid search order.');
  const collator = new Intl.Collator('en', { sensitivity: 'base', numeric: true });
  const numeric = ['downloads', 'year', 'duration'].includes(sort.field);
  const date = ['added', 'updated'].includes(sort.field);
  const valueFor = (chart) => {
    const value = chart?.[sort.field === 'duration' ? 'durationSeconds' : sort.field];
    if (numeric) return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null;
    if (date) { const time = typeof value === 'string' && /^\d{4}-\d{2}-\d{2}(?:T|$)/.test(value) ? Date.parse(value) : NaN; return Number.isFinite(time) ? time : null; }
    return compact(value) || null;
  };
  return charts.map((chart, index) => ({ chart, index, value: valueFor(chart) })).sort((a, b) => {
    // Unknowns belong last in both directions, never masquerading as zero.
    if (a.value === null || b.value === null) return a.value === b.value ? a.index - b.index : a.value === null ? 1 : -1;
    const order = numeric || date ? a.value - b.value : collator.compare(a.value, b.value);
    return order ? order * (sort.direction === 'desc' ? -1 : 1) : a.index - b.index;
  }).map(({ chart }) => chart);
}

module.exports = { SORT_FIELDS, PARTS, EMPTY_FILTERS, normalizeSearchRequest, searchIdentity, hasLocalFilters, parseParts, chartFilterDecision, chartMatchesFilters, filterCharts, sortCharts };
