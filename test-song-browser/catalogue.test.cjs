'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { normalizeSearchRequest, searchIdentity, hasLocalFilters, parseParts, chartFilterDecision, filterCharts, sortCharts } = require('../electron/song-browser/catalogue.cjs');

test('arrangement badges decode compact and separated path codes without matching unrelated words', () => {
  for (const value of ['LRB', 'lrb', 'L R B', 'L/R/B', 'Lead, Rhythm, Bass', ['L', 'R', 'B']]) {
    assert.deepEqual(parseParts(value), ['lead', 'rhythm', 'bass'], String(value));
  }
  for (const [value, expected] of [['L', ['lead']], ['R', ['rhythm']], ['B', ['bass']], ['LR', ['lead', 'rhythm']], ['LB', ['lead', 'bass']], ['RB', ['rhythm', 'bass']]]) {
    assert.deepEqual(parseParts(value), expected);
  }
  for (const value of ['', 'Unknown', 'Bridge', 'Blur', 'LRBX', 'BB', 'Rhythmical', 'Leadbetter', ['guitar'], null]) assert.deepEqual(parseParts(value), []);
});

test('Green Lung LRB metadata satisfies each requested arrangement and all checked paths', () => {
  const songs = [{ id: '62795', title: 'Leaders Of The Blind', artist: 'Green Lung', parts: 'LRB', arrangements: [] },
    { id: '2', parts: 'L' }, { id: '3', parts: 'B' }, { id: '4', parts: 'Unknown' }];
  for (const [parts, expected] of [[['lead'], ['62795', '2']], [['rhythm'], ['62795']], [['bass'], ['62795', '3']], [['lead', 'bass'], ['62795']], [['lead', 'rhythm', 'bass'], ['62795']]]) {
    assert.deepEqual(filterCharts(songs, { parts }).map((song) => song.id), expected);
  }
});

test('search contracts validate bounds and normalize equivalent search definitions', () => {
  const request = normalizeSearchRequest({ query: '  Iron   Maiden  ', page: 2, sort: { field: 'downloads', direction: 'desc' }, filters: { parts: ['bass', 'lead'], exactArtist: 'IRON MAIDEN' } });
  assert.equal(request.query, 'Iron Maiden');
  assert.deepEqual(request.filters.parts, ['lead', 'bass']);
  assert.equal(searchIdentity(request), searchIdentity({ ...request, page: 3, query: 'iron maiden' }));
  assert.notEqual(searchIdentity(request, { includePage: true }), searchIdentity({ ...request, page: 3 }, { includePage: true }));
  assert.equal(searchIdentity(request, { includeSort: false }), searchIdentity({ ...request, sort: { field: 'title', direction: 'asc' } }, { includeSort: false }));
  for (const invalid of [null, {}, { query: 'a' }, { query: 'a'.repeat(161) }, { query: 'Song', page: 0 }, { query: 'Song', page: 1.5 }, { query: 'Song', sort: { field: 'rating' } }, { query: 'Song', filters: { parts: ['guitar'] } }, { query: 'Song', filters: { availableOnly: 'true' } }, { query: 'Song\nName' }]) assert.throws(() => normalizeSearchRequest(invalid), TypeError);
  assert.equal(hasLocalFilters(normalizeSearchRequest('Song').filters), false);
  assert.equal(hasLocalFilters(normalizeSearchRequest({ query: 'Song', filters: { hideConverted: true } }).filters), true);
});

test('any selected arrangements finds either path while all still requires every path', () => {
  const charts = [{ id: 'lead', parts: 'L', artist: 'Green Lung' }, { id: 'rhythm', arrangements: ['rhythm'], artist: 'Green Lung' },
    { id: 'both', parts: 'LRB', artist: 'Green Lung' }, { id: 'bass', parts: 'B', artist: 'Green Lung' },
    { id: 'unknown', parts: 'Unknown', artist: 'Green Lung' }, { id: 'other', parts: 'L', artist: 'Another Artist' }];
  const filters = { parts: ['lead', 'rhythm'], partsMatch: 'any', exactArtist: 'Green Lung' };
  assert.deepEqual(filterCharts(charts, filters).map((chart) => chart.id), ['lead', 'rhythm', 'both']);
  assert.deepEqual(filterCharts(charts, { ...filters, partsMatch: 'all' }).map((chart) => chart.id), ['both']);
  assert.deepEqual(chartFilterDecision(charts[4], filters), { matches: false, unknown: ['parts'], excluded: [] });
  for (const partsMatch of ['any', 'all']) {
    assert.deepEqual(filterCharts(charts, { parts: ['lead'], partsMatch }).map((chart) => chart.id), ['lead', 'both', 'other']);
    assert.deepEqual(filterCharts(charts, { parts: [], partsMatch }), charts, 'No checked arrangements is unrestricted.');
  }
});

test('arrangement match mode is validated and separates cached search identities', () => {
  const all = { query: 'Green Lung', filters: { parts: ['lead', 'rhythm'] } };
  const any = { ...all, filters: { ...all.filters, partsMatch: 'any' } };
  assert.equal(normalizeSearchRequest(all).filters.partsMatch, 'all');
  assert.equal(normalizeSearchRequest(any).filters.partsMatch, 'any');
  assert.equal(searchIdentity(all), searchIdentity({ ...all, filters: { ...all.filters, partsMatch: 'all' } }));
  assert.notEqual(searchIdentity(all), searchIdentity(any));
  assert.notEqual(searchIdentity(all, { includeSort: false }), searchIdentity(any, { includeSort: false }));
  for (const partsMatch of ['or', 'AND', '', true, [], {}]) {
    assert.throws(() => normalizeSearchRequest({ ...all, filters: { ...all.filters, partsMatch } }), TypeError);
  }
  assert.equal(hasLocalFilters(normalizeSearchRequest({ query: 'Song', filters: { partsMatch: 'any' } }).filters), false);
});

test('exact artist, path and tuning requirements cannot be overridden by popularity', () => {
  const charts = [
    { id: '1', artist: 'Meshuggah', title: 'Beneath', parts: 'Lead', tuning: 'Bb Standard', downloads: 261 },
    { id: '2', artist: 'Meshuggah', title: 'Beneath', parts: 'Bass', tuning: 'Bb Standard', downloads: 10000 },
    { id: '3', artist: 'Meshuggah tribute', title: 'Beneath', parts: 'Lead', tuning: 'Bb Standard', downloads: 20000 },
    { id: '4', artist: 'Meshuggah', title: 'Beneath', parts: 'Lead', tuning: 'E Standard', downloads: 50000 },
    { id: '5', artist: 'Meshuggah', title: 'Beneath', parts: 'Lead', tuning: 'Multiple tunings', downloads: 90000 },
  ];
  const { filters } = normalizeSearchRequest({ query: 'Meshuggah', filters: { exactArtist: 'meshuggah', parts: ['lead'], tuning: 'Bb Standard' } });
  assert.deepEqual(sortCharts(filterCharts(charts, filters), { field: 'downloads', direction: 'desc' }).map((chart) => chart.id), ['1']);
  assert.deepEqual(chartFilterDecision(charts[4], filters).unknown, ['tuning']);
  assert.deepEqual(chartFilterDecision({ artist: 'Meshuggah' }, filters).unknown, ['parts', 'tuning']);
});

test('local exclusion filters preserve unknown status and accept only explicit output availability', () => {
  const charts = [{ id: '1', reported: true }, { id: '2', abandoned: true }, { id: '3', alreadyConverted: true }, { id: '4', reported: null, abandoned: null }, { id: '5', reported: false, abandoned: false }];
  assert.deepEqual(filterCharts(charts, { hideReported: true, hideAbandoned: true, hideConverted: true }).map((chart) => chart.id), ['4', '5']);
  assert.deepEqual(filterCharts([{ id: '1', supported: true }, { id: '2', supported: null }, { id: '3', supported: false }], { availableOnly: true }).map((chart) => chart.id), ['1']);
});

test('sorts complete snapshots stably with unknown metadata last in either direction', () => {
  const charts = [
    { id: '1', title: 'Song 10', downloads: null, updated: null },
    { id: '2', title: 'Song 2', downloads: 100, updated: '2026-01-01' },
    { id: '3', title: 'Song 2', downloads: 0, updated: '2025-12-31' },
  ];
  assert.deepEqual(sortCharts(charts).map((chart) => chart.id), ['2', '3', '1']);
  assert.deepEqual(sortCharts(charts, { field: 'downloads', direction: 'asc' }).map((chart) => chart.id), ['3', '2', '1']);
  assert.deepEqual(sortCharts(charts, { field: 'downloads', direction: 'desc' }).map((chart) => chart.id), ['2', '3', '1']);
  assert.deepEqual(sortCharts(charts, { field: 'updated', direction: 'desc' }).map((chart) => chart.id), ['2', '3', '1']);
  assert.deepEqual(charts.map((chart) => chart.id), ['1', '2', '3'], 'source order is immutable');
  const large = Array.from({ length: 1200 }, (_, i) => ({ id: String(i), downloads: i }));
  const sorted = sortCharts(large, { field: 'downloads', direction: 'desc' });
  assert.equal(sorted.length, 1200);
  assert.equal(sorted[0].id, '1199');
  assert.equal(sorted.at(-1).id, '0');
});
