'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { selectRevision, resolveRevision, MAX_HISTORY_BYTES } = require('../electron/song-browser/providers/songsterr/revisions.cjs');

// The observed Fear Of The Dark history, reduced to moderation/identity fields.
function fixture() {
  const descriptor = { id: '492398', title: 'Fear Of The Dark', artist: 'Iron Maiden' };
  const row = (revisionId, createdAt, extra = {}) => ({ songId: 492398, revisionId, createdAt,
    title: descriptor.title, artist: descriptor.artist, author: { personId: 1 },
    isDeleted: false, isBlocked: false, isOnModeration: false, moderationType: 'pre',
    reviewed: { conclusion: 'average' }, ...extra });
  const history = [
    row(8246923, '2026-07-31T12:00:00Z', { isBlocked: true, reviewed: { conclusion: 'rejected' } }),
    row(7509113, '2026-06-22T19:13:13.314Z', { author: { personId: 2588131, isModerator: true }, moderationType: 'no', reviewed: null }),
    row(7470773, '2026-06-20T12:00:00Z'),
  ];
  const metadata = { ...descriptor, songId: 492398, revisionId: 7509113, latestRevisionId: 7509113,
    isPublished: true, author: history[1].author };
  const page = { songId: descriptor.id, historyReady: true, revisionRows: [
    { revisionId: '8246923', approved: false, excluded: true, moderator: false },
    { revisionId: '7509113', approved: false, excluded: false, moderator: true },
    { revisionId: '7470773', approved: true, excluded: false, moderator: false },
  ] };
  return { descriptor, metadata, history, page };
}
const choose = f => selectRevision(f.descriptor, f.metadata, f.history, f.page);
const reject = f => assert.throws(() => choose(f), { code: 'unapproved_revision' });
function onlyModerator() { const f = fixture(); f.history.splice(2); f.page.revisionRows.splice(2); return f; }

test('current published moderator correction beats the older approved but broken revision', () => {
  const f = fixture(), selected = choose(f);
  assert.equal(selected.revisionId, '7509113');
  assert.equal(selected.revisionEvidence.basis, 'moderator_default');
  assert.equal(selected.revisionEvidence.authorId, '2588131');
  assert.equal(selected.revisionEvidence.defaultRevisionId, '7509113');
  assert.ok(Number.isFinite(Date.parse(selected.revisionEvidence.checkedAt)));
  f.history.reverse(); f.page.revisionRows.reverse();
  assert.equal(choose(f).revisionId, '7509113', 'Selection is by timestamp, not row order.');
});

for (const conclusion of ['approved', 'fair', 'average', 'good', 'excellent']) test(`completed ${conclusion} review remains eligible`, () => {
  const f = fixture(); f.history[1].author.isModerator = false;
  f.history[2].reviewed.conclusion = conclusion;
  const out = choose(f);
  assert.equal(out.revisionId, '7470773'); assert.equal(out.revisionEvidence.basis, 'reviewed');
  assert.equal(out.revisionEvidence.reviewConclusion, conclusion);
});

const moderatorDisqualifiers = {
  'no moderator role': f => { f.history[1].author.isModerator = false; },
  'unknown moderator role': f => { delete f.history[1].author.isModerator; },
  'moderator text is not a boolean': f => { f.history[1].author.isModerator = 'true'; },
  'not current default': f => { f.metadata.revisionId = 7470773; },
  'latest identity disagrees': f => { f.metadata.latestRevisionId = 7470773; },
  'no latest identity': f => { delete f.metadata.latestRevisionId; },
  'different default author': f => { f.metadata.author = { personId: 123, isModerator: true }; },
  'default author not moderator': f => { f.metadata.author = { personId: 2588131 }; },
  'unknown moderation mode': f => { f.history[1].moderationType = 'future'; },
  'preapproval awaiting review': f => { f.history[1].moderationType = 'post'; },
  'pending moderation': f => { f.history[1].isOnModeration = true; },
  'blocked correction': f => { f.history[1].isBlocked = true; },
  'deleted correction': f => { f.history[1].isDeleted = true; },
  'missing moderation flag': f => { delete f.history[1].isOnModeration; },
  'string moderation flag': f => { f.history[1].isBlocked = 'false'; },
  'rejected review on moderator entry': f => { f.history[1].reviewed = { conclusion: 'rejected' }; },
  'empty review object': f => { f.history[1].reviewed = {}; },
  'unknown review conclusion': f => { f.history[1].reviewed = { conclusion: 'future' }; },
  'missing visible moderator marker': f => { f.page.revisionRows[1].moderator = false; },
  'visible pending or alternative label': f => { f.page.revisionRows[1].excluded = true; },
  'missing visible exclusion evidence': f => { delete f.page.revisionRows[1].excluded; },
  'missing matching history row': f => { f.page.revisionRows.splice(1, 1); },
  'duplicate visible revision': f => { f.page.revisionRows.push({ ...f.page.revisionRows[1] }); },
  'changed song title': f => { f.history[1].title = 'Another Song'; },
  'invalid creation date': f => { f.history[1].createdAt = 'unknown'; },
  'default marked pending': f => { f.metadata.isOnModeration = true; },
  'default marked deleted': f => { f.metadata.isSongDeleted = true; },
};
for (const [name, mutate] of Object.entries(moderatorDisqualifiers)) test(`moderator exception rejects ${name}`, () => {
  const f = onlyModerator(); mutate(f); reject(f);
});

for (const [key, value] of Object.entries({ isDeleted: true, isBlocked: true, isOnModeration: true,
  moderationType: 'unknown', reviewed: { conclusion: 'rejected' } })) test(`Approved badge cannot override ${key}`, () => {
  const f = fixture(); f.history = [f.history[2]];
  Object.assign(f.history[0], { [key]: value }); reject(f);
});

test('ordinary default with no completed review is excluded even when published', () => {
  const f = fixture(); f.history[1].author = { personId: 44 }; f.metadata.author = f.history[1].author;
  assert.equal(choose(f).revisionId, '7470773');
  f.history.pop(); reject(f);
});

test('matching visible Approved evidence is required for a reviewed row', () => {
  const f = fixture(); f.history = [f.history[2]]; f.page.revisionRows[2].approved = false; reject(f);
});

test('ambiguous metadata/history fail closed', () => {
  const changes = [f => { f.metadata.isPublished = false; }, f => { delete f.metadata.isPublished; },
    f => { f.metadata.songId = 1; }, f => { f.metadata.title = 'Other'; }, f => { f.page.songId = '1'; },
    f => { f.page.historyReady = false; }, f => { f.history = []; }, f => { f.history = {}; },
    f => { f.history.push({ ...f.history[0] }); }, f => { f.history[0].songId = 1; },
    f => { f.history[0] = null; }, f => { f.history[0].revisionId = 'wrong'; },
    f => { f.history[2].createdAt = f.history[1].createdAt; }];
  for (const change of changes) { const f = fixture(); change(f); reject(f); }
});

const response = value => new Response(JSON.stringify(value));
test('resolution requests public default and moderation history without credentials or unpublished flags', async () => {
  const f = fixture(), calls = [];
  const out = await resolveRevision(f.descriptor, f.page, { fetch: async (url, options) => {
    calls.push({ url, options }); return response(calls.length === 1 ? f.metadata : f.history);
  } });
  assert.equal(out.revisionId, '7509113');
  assert.deepEqual(calls.map(c => c.url), ['https://www.songsterr.com/api/meta/492398', 'https://www.songsterr.com/api/meta/492398/revisions']);
  for (const { options } of calls) { assert.equal(options.credentials, 'omit'); assert.equal(options.redirect, 'error'); }
});

test('malformed, oversized, unavailable and cancelled status requests cannot select anything', async () => {
  const f = fixture();
  for (const value of [[], {}, '<html>', null]) {
    let n = 0;
    await assert.rejects(resolveRevision(f.descriptor, f.page, { fetch: async () => response(++n === 1 ? f.metadata : value) }));
  }
  await assert.rejects(resolveRevision(f.descriptor, f.page, { fetch: async () => new Response('{}',
    { headers: { 'content-length': String(MAX_HISTORY_BYTES + 1) } }) }), { code: 'score_too_large' });
  await assert.rejects(resolveRevision(f.descriptor, f.page, { fetch: async () => new Response('{}',
    { status: 429, headers: { 'retry-after': '10' } }) }), e => e.code === 'rate_limited' && e.transport.operation === 'score_metadata');
  const controller = new AbortController(); controller.abort();
  await assert.rejects(resolveRevision(f.descriptor, f.page, { signal: controller.signal,
    fetch: async () => { throw new Error('must not run'); } }), { name: 'AbortError' });
});
