'use strict';

// These functions drive only the renderer created by the offline integration
// main. They are serialized into that owned renderer, not sent through CDP or
// used to control another app. Mutations go through production DOM event
// handlers, the production preload, IPC registration and SongJobs queue.
const assert = require('node:assert/strict');

function rendererSnapshot() {
  const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();
  const button = (element) => ({ label: clean(element.textContent), disabled: element.disabled });
  const query = document.querySelector('#sb-query');
  const submit = document.querySelector('.sb-search-form button[type="submit"]');
  const account = document.querySelector('.sb-account button');
  const results = [...document.querySelectorAll('.sb-result')].map((card) => ({
    title: clean(card.querySelector('h3')?.textContent),
    buttons: [...card.querySelectorAll('button')].map(button),
  }));
  const jobs = [...document.querySelectorAll('.sb-job')].map((card) => ({
    title: clean(card.querySelector('.sb-job-heading strong')?.textContent),
    status: clean(card.querySelector('.sb-job-status')?.textContent),
    message: clean(card.querySelector('.sb-job-bottom p')?.textContent),
    buttons: [...card.querySelectorAll('button')].map(button),
  }));
  return {
    hasNavigation: Boolean(document.querySelector('.side-nav')),
    activeView: clean(document.querySelector('.side-nav button.active')?.textContent),
    mounted: Boolean(query), query: query?.value ?? null,
    pending: document.querySelector('.sb-results')?.getAttribute('aria-busy') === 'true',
    searchButton: submit ? button(submit) : null,
    connection: clean(document.querySelector('.sb-connection')?.textContent),
    accountButton: account ? button(account) : null,
    results, jobs,
    alerts: [...document.querySelectorAll('.song-browser [role="alert"]')].map((element) => clean(element.textContent)),
    pendingCount: clean(document.querySelector('.sb-count')?.textContent),
  };
}

function rendererAction(action) {
  const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();
  function clickButton(container, label) {
    if (!container) throw new Error('The requested production UI container is absent.');
    const button = [...container.querySelectorAll('button')].find((element) => clean(element.textContent) === label);
    if (!button) throw new Error(`The production UI button is absent: ${label}`);
    if (button.disabled) throw new Error(`The production UI button is disabled: ${label}`);
    button.click();
  }
  if (action.kind === 'query') {
    const input = document.querySelector('#sb-query');
    if (!input) throw new Error('The production search input is absent.');
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
    if (!setter) throw new Error('The native input value setter is unavailable.');
    // Use the native setter so React observes the same changed value it would
    // observe from typing, then dispatch the ordinary bubbling input event.
    setter.call(input, action.value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
    return;
  }
  if (action.kind === 'navigate') return clickButton(document.querySelector('.side-nav'), action.label);
  if (action.kind === 'search') return clickButton(document.querySelector('.sb-search-form'), 'Search');
  if (action.kind === 'account') return clickButton(document.querySelector('.sb-account'), action.label);
  if (action.kind === 'result') {
    const card = [...document.querySelectorAll('.sb-result')].find((element) => clean(element.querySelector('h3')?.textContent) === action.title);
    return clickButton(card, action.label);
  }
  if (action.kind === 'job') {
    const card = [...document.querySelectorAll('.sb-job')].find((element) =>
      clean(element.querySelector('.sb-job-heading strong')?.textContent) === action.title
      && (!action.status || clean(element.querySelector('.sb-job-status')?.textContent) === action.status));
    return clickButton(card, action.label);
  }
  throw new Error('Unsupported renderer test action.');
}

async function runUiScenarios({ win, fixture, test }) {
  assert.ok(win?.webContents && typeof win.webContents.executeJavaScript === 'function');
  assert.equal(typeof test, 'function');
  for (const method of ['holdNextSearch', 'waitForSearch', 'releaseSearch', 'waitForSearchSettled', 'counts', 'waitForDownload', 'failConversionOnce', 'removeOutput']) {
    assert.equal(typeof fixture?.[method], 'function', `The main-side fixture must provide ${method}().`);
  }
  for (const name of ['slow', 'fast', 'retry']) assert.ok(fixture.charts?.[name]?.id && fixture.charts[name].title);
  const { slow, fast, retry } = fixture.charts;
  const started = Date.now();
  const evidence = {};
  let latest;
  const evaluate = (fn, argument) => win.webContents.executeJavaScript(`(${fn.toString()})(${argument === undefined ? '' : JSON.stringify(argument)})`);
  const read = async () => { latest = await evaluate(rendererSnapshot); return latest; };
  const action = (value) => evaluate(rendererAction, value);
  const countFor = (counts, kind, chart) => Number(counts[kind]?.[String(chart.id)] || 0);
  const resultFor = (state, chart) => state.results.find((item) => item.title === chart.title);
  const jobFor = (state, chart, status) => state.jobs.find((item) => item.title === chart.title && (!status || item.status === status));
  const buttonWith = (item, label) => item?.buttons.find((button) => button.label === label);

  async function bounded(promise, label, timeoutMs = 15000) {
    const remaining = Math.min(timeoutMs, 85000 - (Date.now() - started));
    assert.ok(remaining > 0, 'Production renderer scenarios exceeded their 85-second budget.');
    let timer;
    try {
      return await Promise.race([promise, new Promise((_resolve, reject) => {
        timer = setTimeout(() => reject(new Error(`${label} timed out. Last renderer state: ${JSON.stringify(latest)}`)), remaining);
      })]);
    } finally { clearTimeout(timer); }
  }
  async function until(predicate, label) {
    const deadline = Math.min(started + 85000, Date.now() + 15000);
    for (;;) {
      const remaining = deadline - Date.now();
      if (remaining <= 0) throw new Error(`${label} timed out. Last renderer state: ${JSON.stringify(latest)}`);
      // Bound this one read. A timeout exits the loop rather than leaving an
      // orphan polling promise issuing scripts while the main cleans up.
      const state = await bounded(read(), label, remaining);
      if (predicate(state)) return state;
      await new Promise((resolve) => setTimeout(resolve, Math.min(40, Math.max(1, deadline - Date.now()))));
    }
  }
  async function navigate(label) {
    await action({ kind: 'navigate', label });
    return until((state) => state.activeView === label && state.mounted === (label === 'Find songs'), `Navigate to ${label}`);
  }

  await test('production renderer does not infer sign-out before checking CustomsForge', async () => {
    await until((state) => state.hasNavigation, 'Production app navigation');
    await navigate('Find songs');
    const initial = await until((state) => state.connection === 'Connection not checked', 'Unchecked CustomsForge connection');
    assert.equal(initial.accountButton?.label, 'Open browser');
    assert.equal(initial.accountButton?.disabled, false);
    assert.equal((await fixture.counts()).search, 0, 'Checking the UI must not request a website page.');
    evidence.initialConnection = { label: initial.connection, action: initial.accountButton.label };
  });

  await test('production renderer retains an in-flight search across section navigation', async () => {
    await until((state) => state.hasNavigation, 'Production app navigation');
    await navigate('Find songs');
    assert.equal((await fixture.counts()).search, 0, 'Mounting Find songs must not search automatically.');
    await fixture.holdNextSearch();
    await action({ kind: 'query', value: 'fixture' });
    await until((state) => state.query === 'fixture' && !state.searchButton?.disabled, 'Editable production search');
    await action({ kind: 'search' });
    await bounded(fixture.waitForSearch(), 'Held real browser search');
    await until((state) => state.pending && state.searchButton?.disabled, 'Pending search indicator');
    await navigate('Convert');
    const returned = await navigate('Find songs');
    assert.equal(returned.query, 'fixture');
    assert.equal(returned.pending, true);
    assert.equal(returned.searchButton.disabled, true);
    assert.equal((await fixture.counts()).search, 1, 'Returning to a pending search must not duplicate its IPC/browser request.');
    evidence.pendingSearchRetained = true;
  });

  await test('production renderer retains resolved results and an unsent draft after remount', async () => {
    const draft = 'unsent fixture draft';
    await action({ kind: 'query', value: draft });
    await until((state) => state.query === draft, 'Draft input');
    await navigate('Convert');
    await fixture.releaseSearch();
    await bounded(fixture.waitForSearchSettled(), 'Search settling while Find songs is unmounted');
    assert.equal((await read()).mounted, false);
    await navigate('Find songs');
    const resolved = await until((state) => !state.pending && [slow, fast, retry].every((chart) => resultFor(state, chart)), 'Resolved production search results');
    assert.equal(resolved.query, draft, 'Settling a submitted search must preserve a later unsent draft.');
    const titles = resolved.results.map((item) => item.title);
    const unsupported = resolved.results.filter((item) => buttonWith(item, 'Host not supported'));
    assert.equal(unsupported.length, 1, 'The offline fixture must show its unsupported-host chart.');
    assert.equal(buttonWith(unsupported[0], 'Host not supported').disabled, true);
    await navigate('Convert');
    await navigate('Find songs');
    const remounted = await until((state) => !state.pending && state.results.length === titles.length, 'Retained resolved results');
    assert.equal(remounted.query, draft);
    assert.deepEqual(remounted.results.map((item) => item.title), titles);
    const counts = await fixture.counts();
    assert.equal(counts.search, 1);
    assert.equal(counts.searchCompleted, 1);
    evidence.search = { submitted: 'fixture', draft, resultTitles: titles, unsupportedTitle: unsupported[0].title, requests: counts.search, completed: counts.searchCompleted };
  });

  await test('production Cancel action settles a real download and unblocks the next queue job', async () => {
    await action({ kind: 'result', title: slow.title, label: 'Download & convert' });
    await bounded(fixture.waitForDownload(slow.id), 'Real cancellable DownloadItem');
    await until((state) => jobFor(state, slow, 'Downloading'), 'Downloading queue card');
    await action({ kind: 'result', title: fast.title, label: 'Download & convert' });
    await until((state) => jobFor(state, fast, 'Queued'), 'Second queued chart');
    const before = await fixture.counts();
    assert.equal(countFor(before, 'downloads', slow), 1);
    assert.equal(countFor(before, 'downloads', fast), 0, 'The queued chart cannot download before the active transfer settles.');
    await action({ kind: 'job', title: slow.title, status: 'Downloading', label: 'Cancel' });
    const completed = await until((state) => jobFor(state, slow, 'Cancelled') && jobFor(state, fast, 'FeedPak ready'), 'Cancellation and queue progress');
    assert.equal(buttonWith(resultFor(completed, slow), 'Download & convert')?.disabled, false);
    assert.equal(buttonWith(resultFor(completed, fast), 'FeedPak ready')?.disabled, false);
    const after = await fixture.counts();
    assert.equal(countFor(after, 'downloads', slow), 1);
    assert.equal(countFor(after, 'downloads', fast), 1);
    assert.equal(countFor(after, 'conversions', slow), 0);
    assert.equal(countFor(after, 'conversions', fast), 1);
    evidence.cancellation = { cancelledChart: slow.id, completedChart: fast.id, downloadsBeforeCancel: before.downloads, downloadsAfter: after.downloads };
  });

  await test('production Retry conversion action uses the retained PSARC without another download', async () => {
    await fixture.failConversionOnce(retry.id);
    await action({ kind: 'result', title: retry.title, label: 'Download & convert' });
    const failed = await until((state) => buttonWith(jobFor(state, retry, 'Failed'), 'Retry conversion'), 'Cached conversion failure');
    assert.equal(buttonWith(jobFor(failed, retry, 'Failed'), 'Open in FeedForge')?.disabled, false);
    const before = await fixture.counts();
    assert.equal(countFor(before, 'downloads', retry), 1);
    assert.equal(countFor(before, 'conversions', retry), 1);
    await action({ kind: 'job', title: retry.title, status: 'Failed', label: 'Retry conversion' });
    const completed = await until((state) => jobFor(state, retry, 'FeedPak ready'), 'Cached retry completion');
    assert.equal(buttonWith(resultFor(completed, retry), 'FeedPak ready')?.disabled, false);
    const after = await fixture.counts();
    assert.equal(countFor(after, 'downloads', retry), 1, 'Retry conversion must not request another host transfer.');
    assert.equal(countFor(after, 'conversions', retry), 2);
    evidence.cachedRetry = { chart: retry.id, downloads: countFor(after, 'downloads', retry), conversions: countFor(after, 'conversions', retry) };
  });

  await test('production Show file action refreshes unavailable output state and restores the download action', async () => {
    const before = await fixture.counts();
    await fixture.removeOutput(retry.id);
    const stale = await read();
    assert.ok(jobFor(stale, retry, 'FeedPak ready'), 'Deleting the owned output alone must not manufacture a renderer state event.');
    await action({ kind: 'job', title: retry.title, status: 'FeedPak ready', label: 'Show file' });
    const unavailable = await until((state) => jobFor(state, retry, 'File unavailable')
      && state.alerts.some((message) => /no longer available/i.test(message))
      && buttonWith(resultFor(state, retry), 'Download & convert')?.disabled === false, 'Fresh missing-output UI state');
    assert.equal(buttonWith(jobFor(unavailable, retry, 'File unavailable'), 'Show file'), undefined);
    const after = await fixture.counts();
    assert.equal(after.reveals, before.reveals, 'The native reveal operation must not run for a missing file.');
    assert.equal(countFor(after, 'downloads', retry), countFor(before, 'downloads', retry), 'A missing-output event must not automatically download the chart.');
    assert.equal(countFor(after, 'conversions', retry), countFor(before, 'conversions', retry));
    evidence.missingOutput = { chart: retry.id, status: 'File unavailable', explicitDownloadRestored: true, nativeRevealCalls: after.reveals };
  });

  await test('production account controls distinguish search failures from an observed sign-out and recover', async () => {
    async function submit(query, expectedConnection, expectedAction) {
      await action({ kind: 'query', value: query });
      await until((state) => state.query === query && !state.searchButton?.disabled, 'Search input for ' + query);
      await action({ kind: 'search' });
      const settled = await until((state) => !state.pending && state.connection === expectedConnection, 'Connection result for ' + query);
      assert.equal(settled.accountButton?.label, expectedAction);
      assert.equal(settled.accountButton?.disabled, false);
      return settled;
    }
    const connected = await read();
    assert.equal(connected.connection, 'Connected');
    assert.equal(connected.accountButton?.label, 'Open browser');
    const failed = await submit('network failure', 'Search unavailable', 'Open browser');
    assert.ok(failed.alerts.length, 'The failed load must report its search error.');
    assert.equal(failed.results.length, 0);
    const beforeOpen = await fixture.counts();
    await action({ kind: 'account', label: 'Open browser' });
    await until((state) => state.accountButton?.label === 'Open browser' && !state.accountButton.disabled, 'Browser action after a search failure');
    const afterOpen = await fixture.counts();
    assert.equal(afterOpen.browserActions.showBrowser, beforeOpen.browserActions.showBrowser + 1);
    assert.equal(afterOpen.browserActions.signIn, beforeOpen.browserActions.signIn, 'A search error must not invoke Sign in.');

    await submit('login', 'Signed out', 'Sign in');
    const beforeSignIn = await fixture.counts();
    await action({ kind: 'account', label: 'Sign in' });
    await until((state) => state.accountButton?.label === 'Sign in' && !state.accountButton.disabled, 'Explicit sign-in action');
    const afterSignIn = await fixture.counts();
    assert.equal(afterSignIn.browserActions.signIn, beforeSignIn.browserActions.signIn + 1);
    assert.equal(afterSignIn.browserActions.showBrowser, beforeSignIn.browserActions.showBrowser);

    await submit('challenge', 'Browser check needed', 'Open browser');
    const recovered = await submit('fixture', 'Connected', 'Open browser');
    assert.ok(recovered.results.length > 0, 'A successful retry must restore the song results.');
    assert.deepEqual(recovered.alerts, []);
    evidence.connectionRecovery = { failedLoad: 'Search unavailable', confirmedLogin: 'Signed out', challenge: 'Browser check needed', recovered: 'Connected', signInInvokedForError: false };
  });
  return { ...evidence, durationMs: Date.now() - started };
}

module.exports = { runUiScenarios };
