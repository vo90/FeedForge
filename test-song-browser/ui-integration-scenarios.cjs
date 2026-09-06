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
    text: clean(card.textContent),
    host: clean(card.querySelector('.sb-host')?.textContent),
    selectionDisabled: card.querySelector('.sb-chart-select')?.disabled,
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
    outputFolder: clean(document.querySelector('.sb-output-label p')?.textContent),
    outputButtons: [...document.querySelectorAll('.sb-output button')].map(button),
    settingsSection: clean(document.querySelector('.settings-nav button.active')?.textContent),
    settingsOutputFolder: clean(document.querySelector('.settings-page .path-action.wide b')?.textContent),
    results, jobs,
    alerts: [...document.querySelectorAll('.song-browser [role="alert"]')].map((element) => clean(element.textContent)),
    pendingCount: clean(document.querySelector('.sb-count')?.textContent),
    sort: document.querySelector('select[aria-label="Sort results"]')?.value,
    direction: document.querySelector('select[aria-label="Sort direction"]')?.value,
    exactArtist: document.querySelector('input[aria-label="Exact artist"]')?.value,
    searchParts: [...document.querySelectorAll('.sb-search-form .sb-filter-checks label')].filter((label) => label.querySelector('input:checked')).map((label) => clean(label.textContent)),
    pagination: clean(document.querySelector('.sb-pagination span')?.textContent),
    resultCount: clean(document.querySelector('.sb-results .sb-section-heading [role="status"]')?.textContent),
    selectionCount: clean(document.querySelector('.sb-batch-prepare p')?.textContent),
    advancedPreferencesPresent: Boolean(document.querySelector('.sb-requirements, .sb-instrument-requirements, [aria-label*="backing" i], [aria-label*=" strings" i], [aria-label*=" instrument" i]')),
    searchProgress: [...document.querySelectorAll('.sb-search-form [role="status"]')].map((element) => clean(element.textContent)),
    batches: [...document.querySelectorAll('.sb-batch')].map((batch) => ({ summary: clean(batch.querySelector('summary')?.textContent), text: clean(batch.textContent), status: [...batch.querySelectorAll('[role="status"]')].map((element) => clean(element.textContent)), buttons: [...batch.querySelectorAll('button')].map(button),
      selected: [...batch.querySelectorAll('.sb-batch-option input:checked')].map((input) => input.getAttribute('aria-label')),
      options: [...batch.querySelectorAll('.sb-batch-option')].map((label) => ({ label: label.querySelector('input')?.getAttribute('aria-label'), checked: label.querySelector('input')?.checked, disabled: label.querySelector('input')?.disabled, text: clean(label.textContent) })),
      suggestions: [...batch.querySelectorAll('.sb-duplicate-suggestion summary')].map((element) => clean(element.textContent)),
      items: [...batch.querySelectorAll('.sb-batch-item')].map((element) => ({ title: clean(element.querySelector('strong')?.textContent), text: clean(element.textContent), buttons: [...element.querySelectorAll('button')].map(button) })) })),
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
  if (action.kind === 'output') return clickButton(document.querySelector('.sb-output'), action.label);
  if (action.kind === 'settingsOutput') {
    const button = [...document.querySelectorAll('.settings-page button.path-action')].find((element) => clean(element.querySelector('span')?.textContent) === 'Output');
    if (!button || button.disabled) throw new Error('The Settings Output folder control is absent or disabled.');
    button.click(); return;
  }
  if (action.kind === 'setting') {
    const label = [...document.querySelectorAll('.settings-page label')].find((element) => clean([...element.childNodes].filter((node) => node.nodeType === Node.TEXT_NODE).map((node) => node.textContent).join(' ')) === action.label);
    const input = label?.querySelector('input, select');
    if (!input || input.disabled) throw new Error('The requested Settings control is absent or disabled: ' + action.label);
    const prototype = input.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(prototype, 'value').set.call(input, action.value);
    input.dispatchEvent(new Event(input.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true }));
    return;
  }
  if (action.kind === 'control') {
    const input = [...document.querySelectorAll('.song-browser input, .song-browser select')].find((element) => element.getAttribute('aria-label') === action.label);
    if (!input) throw new Error('The requested search control is absent: ' + action.label);
    const prototype = input.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(prototype, 'value').set.call(input, action.value);
    input.dispatchEvent(new Event(input.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true }));
    return;
  }
  if (action.kind === 'page') return clickButton(document.querySelector('.sb-pagination'), action.label);
  if (action.kind === 'select') {
    const input = [...document.querySelectorAll('.sb-result input[type="checkbox"]')].find((element) => element.getAttribute('aria-label') === action.label);
    if (!input) throw new Error('The requested result selection is absent: ' + action.label);
    if (typeof action.checked !== 'boolean' || input.checked !== action.checked) input.click(); return;
  }
  if (action.kind === 'prepare') return clickButton(document.querySelector('.sb-batch-prepare'), action.label);
  if (action.kind === 'batch') return clickButton(document.querySelector('.sb-batch'), action.label);
  if (action.kind === 'batchItem') {
    const item = [...document.querySelector('.sb-batch').querySelectorAll('.sb-batch-item')].find((element) => clean(element.querySelector('strong')?.textContent) === action.title);
    return clickButton(item, action.label);
  }
  if (action.kind === 'batchSelect') {
    const input = [...document.querySelector('.sb-batch').querySelectorAll('.sb-batch-option input')].find((element) => element.getAttribute('aria-label') === action.label);
    if (!input || input.disabled) throw new Error('The requested batch selection is absent or disabled.');
    if (typeof action.checked !== 'boolean' || input.checked !== action.checked) input.click(); return;
  }
  if (action.kind === 'checkbox') {
    const scope = action.legend ? [...document.querySelectorAll('fieldset')].find((element) => clean(element.querySelector('legend')?.textContent) === action.legend) : document.querySelector(action.scope || '.song-browser');
    const label = [...scope.querySelectorAll('label')].find((element) => clean(element.textContent) === action.label);
    if (!label?.querySelector('input[type="checkbox"]')) throw new Error('Requested checkbox is absent: ' + action.label);
    const input = label.querySelector('input[type="checkbox"]');
    if (typeof action.checked !== 'boolean' || input.checked !== action.checked) input.click(); return;
  }
  if (action.kind === 'expand') {
    const scope = document.querySelector(action.scope || '.song-browser');
    if (scope.tagName === 'DETAILS') scope.open = true;
    for (const details of scope.querySelectorAll('details')) if (!action.label || clean(details.querySelector('summary')?.textContent) === action.label) details.open = true;
    return;
  }
  if (action.kind === 'searchCancel') return clickButton(document.querySelector('.sb-search-form'), 'Cancel search');
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
  for (const method of ['holdNextSearch', 'waitForSearch', 'releaseSearch', 'waitForSearchSettled', 'counts', 'waitForDownload', 'failConversionOnce', 'removeOutput', 'waitForOutputSettings', 'outputFor', 'chooseOutput']) {
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
    const remaining = Math.min(timeoutMs, 115000 - (Date.now() - started));
    assert.ok(remaining > 0, 'Production renderer scenarios exceeded their 115-second budget.');
    let timer;
    try {
      return await Promise.race([promise, new Promise((_resolve, reject) => {
        timer = setTimeout(() => reject(new Error(`${label} timed out. Last renderer state: ${JSON.stringify(latest)}`)), remaining);
      })]);
    } finally { clearTimeout(timer); }
  }
  async function until(predicate, label) {
    const deadline = Math.min(started + 115000, Date.now() + 15000);
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
  async function setOutputSettings(format, outputLayout, nameTemplate) {
    await navigate('Settings');
    await action({ kind: 'setting', label: 'File names', value: format });
    if (format === 'custom') await action({ kind: 'setting', label: 'Naming template', value: nameTemplate });
    await action({ kind: 'setting', label: 'Output layout', value: outputLayout });
    await navigate('Find songs');
    await bounded(fixture.waitForOutputSettings({ outputLayout, nameTemplate }), 'Shared output settings');
  }

  await test('production renderer does not infer sign-out before checking CustomsForge', async () => {
    await until((state) => state.hasNavigation, 'Production app navigation');
    await navigate('Find songs');
    const initial = await until((state) => state.connection === 'Connection not checked', 'Unchecked CustomsForge connection');
    assert.equal(initial.accountButton?.label, 'Open browser');
    assert.equal(initial.accountButton?.disabled, false);
    assert.equal(initial.advancedPreferencesPresent, false, 'Find songs must expose the straightforward arrangement and tuning filters without the removed advanced preferences.');
    assert.equal((await fixture.counts()).search, 0, 'Checking the UI must not request a website page.');
    assert.equal(initial.outputFolder, 'Choose an output folder in Settings.');
    assert.deepEqual(initial.outputButtons, [{ label: 'Choose in Settings', disabled: false }], 'Find songs must navigate to the shared setting instead of offering an independent folder picker.');
    await action({ kind: 'output', label: 'Choose in Settings' });
    const settings = await until((state) => state.activeView === 'Settings' && state.settingsSection === 'Conversion', 'Output shortcut opens Conversion settings');
    assert.equal(settings.settingsOutputFolder, 'Source folder', 'The test must begin with no explicit output folder in Settings.');
    assert.equal((await fixture.counts()).outputPicks.length, 0, 'Opening Settings cannot open a folder picker by itself.');
    await navigate('Find songs');
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

  await test('production Find songs uses the output folder selected in Settings', async () => {
    const before = await until((state) => state.results.length > 0 && !state.pending, 'Search results without an output folder');
    assert.equal(before.outputFolder, 'Choose an output folder in Settings.');
    assert.equal(before.searchButton.disabled, false, 'Choosing an output folder must not be required for searching.');
    for (const chart of [slow, fast, retry]) assert.equal(buttonWith(resultFor(before, chart), 'Download & convert')?.disabled, true, 'Downloading requires an explicit output folder in Settings.');
    const chosen = [];
    for (const name of ['first', 'second']) {
      if (name === 'first') {
        await action({ kind: 'output', label: 'Choose in Settings' });
        await until((state) => state.activeView === 'Settings' && state.settingsSection === 'Conversion', 'Shared output folder settings');
      } else await navigate('Settings');
      const directory = await fixture.chooseOutput(name);
      await action({ kind: 'settingsOutput' });
      await until((state) => state.settingsOutputFolder === directory, 'Settings displays the selected ' + name + ' folder');
      await navigate('Find songs');
      await bounded(fixture.waitForOutputSettings({ outputDir: directory }), 'Shared ' + name + ' output folder synchronization');
      const state = await until((value) => value.outputFolder === directory && buttonWith(resultFor(value, fast), 'Download & convert')?.disabled === false, 'Find songs uses the selected ' + name + ' folder');
      assert.ok(state.outputButtons.every((button) => button.label !== 'Choose folder'), 'Find songs cannot expose a separate folder picker.');
      chosen.push(directory);
    }
    const counts = await fixture.counts();
    assert.deepEqual(counts.outputPicks.map((item) => item.selected), chosen);
    assert.equal(counts.outputPicks[1].defaultPath, chosen[0], 'The main Settings picker reuses the previous shared output folder.');
    assert.equal(counts.songBrowserFolderPicks, 0, 'No independent Song Browser folder dialog may run.');
    assert.equal(counts.search, 1, 'Changing output settings must preserve the searched catalogue.');
    evidence.sharedOutputFolder = { initial: 'Source folder', selected: chosen, searchAllowedBeforeSelection: true, downloadRequiresSelection: true };
  });

  await test('production Cancel action settles a real download and unblocks the next queue job', async () => {
    await action({ kind: 'result', title: slow.title, label: 'Download & convert' });
    await bounded(fixture.waitForDownload(slow.id), 'Real cancellable DownloadItem');
    await until((state) => jobFor(state, slow, 'Downloading'), 'Downloading queue card');
    await action({ kind: 'result', title: fast.title, label: 'Download & convert' });
    await until((state) => jobFor(state, fast, 'Queued'), 'Second queued chart');
    const queuedOutput = await fixture.outputFor(fast.id, 'queued');
    assert.deepEqual(queuedOutput.outputSettings, { outputLayout: 'flat', nameTemplate: '{source}' });
    await setOutputSettings('artist-title', 'artist', '{artist} - {title}');
    const before = await fixture.counts();
    assert.equal(countFor(before, 'downloads', slow), 1);
    assert.equal(countFor(before, 'downloads', fast), 0, 'The queued chart cannot download before the active transfer settles.');
    await action({ kind: 'job', title: slow.title, status: 'Downloading', label: 'Cancel' });
    await until((state) => jobFor(state, slow, 'Cancelled') && jobFor(state, fast, 'FeedPak ready'), 'Cancellation and queue progress');
    const completedOutput = await fixture.outputFor(fast.id);
    assert.deepEqual(completedOutput.outputSettings, queuedOutput.outputSettings, 'Changing Settings after queueing cannot rename or move an existing job.');
    assert.equal(completedOutput.relativePath, 'fixture-1101.feedpak', 'Source filename uses the observed download name without a CustomsForge ID suffix.');
    assert.equal(completedOutput.outputFolder, 'second', 'The completed download must use the latest folder chosen in Settings.');
    await setOutputSettings('source', 'flat', '{source}');
    const completed = await until((state) => buttonWith(resultFor(state, fast), 'FeedPak ready'), 'Restored naming settings reuse their completed output');
    assert.equal(buttonWith(resultFor(completed, slow), 'Download & convert')?.disabled, false);
    assert.equal(buttonWith(resultFor(completed, fast), 'FeedPak ready')?.disabled, false);
    const after = await fixture.counts();
    assert.equal(countFor(after, 'downloads', slow), 1);
    assert.equal(countFor(after, 'downloads', fast), 1);
    assert.equal(countFor(after, 'conversions', slow), 0);
    assert.equal(countFor(after, 'conversions', fast), 1);
    evidence.cancellation = { cancelledChart: slow.id, completedChart: fast.id, downloadsBeforeCancel: before.downloads, downloadsAfter: after.downloads };
    evidence.queuedOutputSettings = { ...completedOutput, retainedAcrossSettingsChange: true };
  });

  await test('production Retry conversion action uses the retained PSARC without another download', async () => {
    await setOutputSettings('artist-title', 'artist', '{artist} - {title}');
    await fixture.failConversionOnce(retry.id);
    await action({ kind: 'result', title: retry.title, label: 'Download & convert' });
    const failed = await until((state) => buttonWith(jobFor(state, retry, 'Failed'), 'Retry conversion'), 'Cached conversion failure');
    assert.equal(buttonWith(jobFor(failed, retry, 'Failed'), 'Open in FeedForge')?.disabled, false);
    const before = await fixture.counts();
    assert.equal(countFor(before, 'downloads', retry), 1);
    assert.equal(countFor(before, 'conversions', retry), 1);
    await action({ kind: 'job', title: retry.title, status: 'Failed', label: 'Retry conversion' });
    const completed = await until((state) => jobFor(state, retry, 'FeedPak ready') && buttonWith(resultFor(state, retry), 'FeedPak ready'), 'Cached retry completion');
    assert.equal(buttonWith(resultFor(completed, retry), 'FeedPak ready')?.disabled, false);
    const after = await fixture.counts();
    assert.equal(countFor(after, 'downloads', retry), 1, 'Retry conversion must not request another host transfer.');
    assert.equal(countFor(after, 'conversions', retry), 2);
    const output = await fixture.outputFor(retry.id);
    assert.deepEqual(output.outputSettings, { outputLayout: 'artist', nameTemplate: '{artist} - {title}' });
    assert.equal(output.relativePath, 'Fixture Artist/Fixture Artist - Fixture Recovery.feedpak', 'Find songs follows the main Settings naming template and artist folder layout.');
    assert.doesNotMatch(output.relativePath, /\[CF\s|CF[-_ ]1102/);
    evidence.sharedOutputSettings = output;
    evidence.cachedRetry = { chart: retry.id, downloads: countFor(after, 'downloads', retry), conversions: countFor(after, 'conversions', retry) };
    await setOutputSettings('source', 'flat', '{source}');
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
  await test('production sorting spans pages and exact artist filters the complete search', async () => {
    await action({ kind: 'query', value: 'catalogue' });
    await action({ kind: 'search' });
    const initial = await until((state) => !state.pending && state.query === 'catalogue' && state.resultCount === '7 charts', 'Seven-chart paginated catalogue');
    assert.deepEqual(initial.results.map((row) => row.title), ['Fixture Beneath', 'Fixture Beneath']);
    await action({ kind: 'control', label: 'Sort results', value: 'downloads' });
    await action({ kind: 'control', label: 'Sort direction', value: 'desc' });
    await action({ kind: 'search' });
    const sorted = await until((state) => !state.pending && state.results[0]?.title === fast.title, 'Downloads descending across the catalogue');
    assert.deepEqual(sorted.results.map((row) => row.title), [fast.title, retry.title]);
    await action({ kind: 'select', label: `Select ${fast.title} chart ${fast.id}` });
    await action({ kind: 'page', label: 'Next' });
    const next = await until((state) => !state.pending && state.pagination === 'Page 2', 'Second page with the selected sort');
    assert.deepEqual(next.results.map((row) => row.title), [slow.title, 'Fixture Unsupported']);
    assert.match(next.selectionCount, /^1 charts selected/);
    await action({ kind: 'control', label: 'Exact artist', value: 'Different Artist' });
    await action({ kind: 'search' });
    const filtered = await until((state) => !state.pending && state.resultCount === '1 chart', 'Exact artist across all four remote pages');
    assert.equal(filtered.results[0].title, 'Fixture Other Artist');
    assert.equal(filtered.pagination, 'Page 1');
    assert.match(filtered.selectionCount, /^0 charts selected/);
    await navigate('Convert'); await navigate('Find songs');
    const retained = await read();
    assert.equal(retained.sort, 'downloads'); assert.equal(retained.direction, 'desc'); assert.equal(retained.exactArtist, 'Different Artist');
    evidence.catalogue = { sourceCharts: 7, remotePageSize: 2, sortedFirstPage: sorted.results.map((row) => row.title), sortedSecondPage: next.results.map((row) => row.title), exactArtistMatch: filtered.results[0].title, selectionScopeReset: true };
  });

  await test('production batch review runs selected songs and skips a verified existing import', async () => {
    await action({ kind: 'control', label: 'Exact artist', value: '' });
    await action({ kind: 'query', value: 'fixture' });
    await action({ kind: 'search' });
    await until((state) => !state.pending && state.resultCount === '4 charts', 'Unfiltered fixture before batch selection');
    await action({ kind: 'select', label: `Select ${fast.title} chart ${fast.id}` });
    await action({ kind: 'select', label: `Select ${retry.title} chart ${retry.id}` });
    const before = await fixture.counts();
    await action({ kind: 'prepare', label: 'Prepare selected' });
    const draft = await until((state) => state.batches[0]?.summary.startsWith('Review selections'), 'Reviewable two-chart batch');
    assert.match(draft.batches[0].summary, /2 charts/);
    const prepared = await fixture.counts();
    assert.deepEqual(prepared.downloads, before.downloads, 'Preparing alternatives cannot activate a source download.');
    await action({ kind: 'batch', label: 'Start batch (2 charts)' });
    const finished = await until((state) => state.batches[0]?.summary.startsWith('Finished'), 'Serial batch completion');
    assert.ok(finished.batches[0].status.some((status) => status.includes('1 converted') && status.includes('1 already available')));
    const after = await fixture.counts();
    assert.equal(countFor(after, 'downloads', fast), countFor(before, 'downloads', fast), 'Existing valid output is skipped before transfer.');
    assert.equal(countFor(after, 'downloads', retry), countFor(before, 'downloads', retry) + 1, 'Missing output is downloaded again.');
    assert.equal(countFor(after, 'conversions', retry), countFor(before, 'conversions', retry) + 1);
    evidence.batch = { selectedCharts: 2, converted: 1, alreadyAvailable: 1, sourceDownloadsDuringPreparation: 0 };
  });

  await test('production arrangement and tuning filters reassess saved outputs and relaxation retries cached bytes', async () => {
    await until((state) => buttonWith(resultFor(state, fast), 'FeedPak ready'), 'Verified ready result before changing requirements');
    await action({ kind: 'checkbox', scope: '.sb-search-form', label: 'lead' });
    await action({ kind: 'control', label: 'Tuning filter', value: 'E Standard' });
    await action({ kind: 'search' });
    const compatible = await until((state) => !state.pending && state.resultCount === '4 charts' && buttonWith(resultFor(state, fast), 'FeedPak ready'), 'Lead and E Standard reuse the verified compatible output');
    assert.equal(compatible.advancedPreferencesPresent, false);
    await action({ kind: 'checkbox', scope: '.sb-search-form', label: 'rhythm' });
    await action({ kind: 'search' });
    const incompatible = await until((state) => !state.pending && state.resultCount === '1 chart' && buttonWith(resultFor(state, fast), 'Download & convert')?.disabled === false && buttonWith(resultFor(state, fast), 'Show saved FeedPak'), 'Rhythm coverage is checked against the saved file');
    assert.match(resultFor(incompatible, fast).text, /rhythm|arrangement|coverage|requirement/i);
    evidence.requirementsScreenshot = await fixture.captureScreenshot('completion-arrangement-filters', '.sb-search-form');
    const before = await fixture.counts();
    await action({ kind: 'result', title: fast.title, label: 'Review another file/version' });
    await until((state) => state.jobs.some((job) => job.title === fast.title && buttonWith(job, 'Relax requirements and retry cached file')), 'Missing Rhythm arrangement needs attention with cached source');
    const parked = await fixture.counts();
    const parkedJobCount = (await read()).jobs.length;
    assert.equal(countFor(parked, 'downloads', fast), countFor(before, 'downloads', fast) + 1);
    assert.equal(countFor(parked, 'conversions', fast), countFor(before, 'conversions', fast), 'An absent requested arrangement must stop before conversion.');
    await action({ kind: 'job', title: fast.title, status: 'Needs attention', label: 'Relax requirements and retry cached file' });
    await until((state) => state.jobs.length > parkedJobCount && state.jobs[0]?.title === fast.title && state.jobs[0]?.status === 'FeedPak ready', 'Relaxed cached retry completes');
    const retried = await fixture.counts();
    assert.equal(countFor(retried, 'downloads', fast), countFor(parked, 'downloads', fast), 'Relaxing requirements must use the existing cached source.');
    await action({ kind: 'checkbox', scope: '.sb-search-form', label: 'lead' });
    await action({ kind: 'checkbox', scope: '.sb-search-form', label: 'rhythm' });
    await action({ kind: 'control', label: 'Tuning filter', value: '' });
    await action({ kind: 'search' });
    await until((state) => !state.pending && state.resultCount === '4 charts' && buttonWith(resultFor(state, fast), 'FeedPak ready'), 'Cleared arrangement and tuning filters reassess the completed result');
    evidence.requirements = { compatibleLeadAndTuningReused: true, missingRhythmBlocked: true, explicitRelaxation: true, cachedRetryExtraDownloads: 0 };
  });

  await test('production batch Skip is individual and explicit retry requeues a user-skipped song', async () => {
    for (const [chart, checked] of [[fast, true], [retry, false], [slow, true]]) await action({ kind: 'select', label: 'Select ' + chart.title + ' chart ' + chart.id, checked });
    await action({ kind: 'prepare', label: 'Prepare selected' });
    const draft = await until((state) => state.batches[0]?.summary.startsWith('Review selections') && state.batches[0].summary.includes('2 charts'), 'Batch containing available and cancellable songs');
    assert.ok(draft.batches[0].status.some((status) => status.includes('1 planned downloads') && status.includes('1 already available')));
    const before = await fixture.counts();
    await action({ kind: 'batch', label: 'Start batch (2 charts)' });
    await until((state) => state.batches[0]?.items.some((item) => item.title === slow.title && /running/.test(item.text)) && jobFor(state, slow, 'Downloading'), 'Active batch song');
    await fixture.waitForDownload(slow.id, countFor(before, 'downloads', slow) + 1);
    await action({ kind: 'batchItem', title: slow.title, label: 'Skip song' });
    const skipped = await until((state) => state.batches[0]?.summary.startsWith('Finished') && state.batches[0].status.some((status) => status.includes('1 skipped by you') && status.includes('1 already available')), 'Skip cleanup and separate outcome counts');
    assert.ok(skipped.batches[0].items.some((item) => item.title === slow.title && buttonWith(item, 'Retry song')));
    const after = await fixture.counts();
    assert.equal(countFor(after, 'downloads', fast), countFor(before, 'downloads', fast));
    assert.equal(countFor(after, 'downloads', slow), countFor(before, 'downloads', slow) + 1);
    await action({ kind: 'batchItem', title: slow.title, label: 'Retry song' });
    await until((state) => state.batches[0]?.summary.startsWith('Paused') && state.batches[0].status.some((status) => status.includes('1 waiting') && status.includes('0 skipped by you')), 'Explicit retry returns just this song to pending');
    await action({ kind: 'batchItem', title: slow.title, label: 'Skip song' });
    await until((state) => state.batches[0]?.summary.startsWith('Finished') && state.batches[0]?.status.some((status) => status.includes('1 skipped by you')), 'Pending retry skipped before Resume');
    assert.equal(countFor(await fixture.counts(), 'downloads', slow), countFor(after, 'downloads', slow));
    await action({ kind: 'expand', scope: '.sb-batch' });
    evidence.skipScreenshot = await fixture.captureScreenshot('completion-batch-skip', '.sb-batch');
    evidence.skip = { oneAvailable: true, oneUserSkipped: true, activeCleanupCompleted: true, retryRequiresExplicitResume: true };
  });

  await test('production draft recommendations expose creators and possible duplicates without changing manual choices', async () => {
    await action({ kind: 'query', value: 'completion' }); await action({ kind: 'search' });
    await until((state) => !state.pending && state.query === 'completion' && state.results.some((row) => row.title === 'Fixture Success!'), 'Completion review catalogue');
    await action({ kind: 'control', label: 'Preferred creators', value: 'Other Creator, Fixture Creator' });
    await action({ kind: 'prepare', label: 'Prepare all results' });
    const draft = await until((state) => state.batches[0]?.summary.startsWith('Review selections') && state.batches[0].suggestions.length > 0, 'Draft possible-duplicate review');
    assert.ok(draft.batches[0].selected.includes('Select Fixture Success chart 1122'), 'Preferred eligible creator outranks the unlisted alternative.');
    assert.equal(draft.batches[0].suggestions.length, 1, 'The live edition remains separate from the punctuation suggestion.');
    const selectedBeforeDismiss = draft.batches[0].selected;
    await action({ kind: 'expand', scope: '.sb-batch .sb-batch-groups' });
    evidence.draftScreenshot = await fixture.captureScreenshot('completion-expanded-batch-draft', '.sb-batch');
    await action({ kind: 'batch', label: 'Keep selections and dismiss suggestion' });
    const dismissed = await until((state) => state.batches[0]?.suggestions.length === 0, 'Dismissed possible duplicate');
    assert.deepEqual(dismissed.batches[0].selected, selectedBeforeDismiss);
    await action({ kind: 'batchSelect', label: 'Select Fixture Success chart 1122', checked: false });
    await until((state) => !state.batches[0].selected.includes('Select Fixture Success chart 1122'), 'Explicitly uncheck preferred alternative');
    await action({ kind: 'batchSelect', label: 'Select Fixture Success chart ' + fast.id, checked: true });
    await until((state) => state.batches[0].selected.includes('Select Fixture Success chart ' + fast.id), 'Manual chart override');
    await action({ kind: 'expand', scope: '.sb-batch', label: 'Change draft preferences' });
    assert.equal((await read()).advancedPreferencesPresent, false, 'Batch draft editing must not reintroduce removed advanced preferences.');
    await action({ kind: 'control', label: 'Draft ranking', value: 'updated' });
    await action({ kind: 'batch', label: 'Update recommendations' });
    const changed = await until((state) => !state.batches[0].buttons.some((button) => button.label === 'Update recommendations' && button.disabled), 'Updated draft recommendations');
    assert.ok(changed.batches[0].selected.includes('Select Fixture Success chart ' + fast.id));
    assert.ok(!changed.batches[0].selected.includes('Select Fixture Success chart 1122'));
    assert.equal(changed.batches[0].suggestions.length, 0);
    await action({ kind: 'batch', label: 'Remove batch record' });
    evidence.duplicateReview = { suggestedPunctuationOnly: true, livePreserved: true, manualOverrideRetained: true, dismissalRetained: true };
  });

  await test('production arrangement filters and batch review recognize compact LRB catalogue badges', async () => {
    const compact = fixture.compactCharts;
    for (const name of ['all', 'lead', 'rhythm', 'bass']) assert.ok(compact?.[name]?.id && compact[name].title);
    await action({ kind: 'control', label: 'Exact artist', value: '' });
    await action({ kind: 'control', label: 'Sort results', value: 'title' });
    await action({ kind: 'control', label: 'Sort direction', value: 'asc' });
    await action({ kind: 'query', value: 'green lung' });
    await action({ kind: 'search' });
    const initial = await until((state) => !state.pending && state.query === 'green lung' && state.resultCount === '4 charts', 'Compact arrangement catalogue before filtering');
    assert.ok(resultFor(initial, compact.all), 'The Green Lung regression chart must be present before filtering.');
    const before = await fixture.counts();
    const cases = [['lead'], ['rhythm'], ['bass'], ['lead', 'rhythm'], ['lead', 'bass'], ['lead', 'rhythm', 'bass']];
    const matches = [];
    for (const required of cases) {
      for (const part of ['lead', 'rhythm', 'bass']) await action({ kind: 'checkbox', scope: '.sb-search-form', label: part, checked: required.includes(part) });
      await action({ kind: 'search' });
      const expected = [compact.all.title, ...(required.length === 1 ? [compact[required[0]].title] : [])].sort();
      const filtered = await until((state) => !state.pending && state.searchParts.join(',') === required.join(',') && JSON.stringify(state.results.map((row) => row.title).sort()) === JSON.stringify(expected), 'Compact badges match ' + required.join(' + '));
      assert.deepEqual(filtered.alerts, []);
      matches.push({ required, titles: filtered.results.map((row) => row.title) });
    }
    for (const part of ['lead', 'rhythm', 'bass']) await action({ kind: 'checkbox', scope: '.sb-search-form', label: part, checked: false });
    await action({ kind: 'search' });
    await until((state) => !state.pending && state.resultCount === '4 charts', 'Reset compact catalogue filters before batch review');
    await action({ kind: 'checkbox', scope: '.sb-batch-prepare', label: 'lead', checked: true });
    await action({ kind: 'prepare', label: 'Prepare all results' });
    const draft = await until((state) => state.batches[0]?.summary.startsWith('Review selections') && state.batches[0]?.options.some((option) => option.label === `Select ${compact.all.title} chart ${compact.all.id}`), 'Compact LRB batch draft');
    assert.deepEqual([...draft.batches[0].selected].sort(), [compact.all, compact.lead].map((chart) => `Select ${chart.title} chart ${chart.id}`).sort(), 'Required Lead must recommend the LRB chart and Lead-only chart.');
    for (const name of ['all', 'lead', 'rhythm', 'bass']) {
      const chart = compact[name];
      const option = draft.batches[0].options.find((item) => item.label === `Select ${chart.title} chart ${chart.id}`);
      assert.ok(option);
      assert.equal(option.disabled, ['rhythm', 'bass'].includes(name), 'Only charts missing Lead must be ineligible.');
      if (option.disabled) assert.match(option.text, /Does not contain a required arrangement/);
    }
    const after = await fixture.counts();
    assert.deepEqual(after.downloads, before.downloads, 'Filtering and reviewing compact arrangements cannot download a source.');
    assert.deepEqual(after.conversions, before.conversions);
    await action({ kind: 'batch', label: 'Remove batch record' });
    await until((state) => !state.batches.some((batch) => batch.options.some((option) => option.label === `Select ${compact.all.title} chart ${compact.all.id}`)), 'Remove compact arrangement draft');
    await action({ kind: 'checkbox', scope: '.sb-batch-prepare', label: 'lead', checked: false });
    evidence.compactArrangements = { sourceBadge: 'LRB', matches, batchLeadRecommendations: [compact.all.title, compact.lead.title], sourceDownloads: 0 };
  });

  await test('production results identify ODLC and exclude official releases from batch downloads', async () => {
    const { official, custom, unknown } = fixture.officialCharts;
    await action({ kind: 'query', value: 'official fixture' });
    await action({ kind: 'search' });
    const found = await until((state) => !state.pending && state.query === 'official fixture' && state.resultCount === '3 charts', 'Official and custom catalogue fixture');
    const officialRow = resultFor(found, official);
    assert.equal(officialRow.host, 'ODLC');
    assert.equal(buttonWith(officialRow, 'ODLC not downloadable')?.disabled, true);
    assert.equal(officialRow.selectionDisabled, true);
    assert.match(officialRow.text, /Official DLC \(ODLC\) is not available for download from CustomsForge\./);
    assert.equal(buttonWith(officialRow, 'Host not supported'), undefined, 'An identified official release needs a specific explanation.');
    const customRow = resultFor(found, custom);
    assert.equal(customRow.host, 'Dropbox', 'ODLC in a song title is not evidence of an official release.');
    assert.equal(buttonWith(customRow, 'Download & convert')?.disabled, false);
    assert.equal(customRow.selectionDisabled, false);
    const unknownRow = resultFor(found, unknown);
    assert.notEqual(unknownRow.host, 'ODLC', 'An unidentified host is not evidence of an official release.');
    assert.equal(buttonWith(unknownRow, 'Host not supported')?.disabled, true);
    const before = await fixture.counts();
    await action({ kind: 'prepare', label: 'Prepare all results' });
    const draft = await until((state) => state.batches[0]?.summary.startsWith('Review selections') && state.batches[0]?.options.some((option) => option.label === `Select ${official.title} chart ${official.id}`), 'Official release shown in batch review');
    assert.deepEqual(draft.batches[0].selected, [`Select ${custom.title} chart ${custom.id}`]);
    const officialOption = draft.batches[0].options.find((option) => option.label === `Select ${official.title} chart ${official.id}`);
    assert.equal(officialOption.disabled, true);
    assert.match(officialOption.text, /Official DLC \(ODLC\)/);
    assert.deepEqual((await fixture.counts()).downloads, before.downloads);
    await action({ kind: 'batch', label: 'Remove batch record' });
    await action({ kind: 'checkbox', scope: '.sb-search-form', label: 'Available hosts', checked: true });
    await action({ kind: 'search' });
    const available = await until((state) => !state.pending && state.resultCount === '1 chart' && state.results[0]?.title === custom.title, 'Available-host filter excludes ODLC and unidentified hosts');
    assert.equal(available.results[0].host, 'Dropbox');
    await action({ kind: 'checkbox', scope: '.sb-search-form', label: 'Available hosts', checked: false });
    evidence.officialDlc = { label: officialRow.host, downloadDisabled: true, individualSelectionDisabled: true, excludedFromBatch: true, titleOnlyMarkerIgnored: true, unknownHostPreserved: true, sourceDownloads: 0 };
  });

  await test('production filtered-search collection shows scoped progress and can be cancelled', async () => {
    await action({ kind: 'query', value: 'catalogue' });
    await action({ kind: 'control', label: 'Exact artist', value: 'Fixture Artist' });
    await action({ kind: 'search' });
    const collecting = await until((state) => state.pending && state.searchProgress.some((text) => /Collected [1-9]/.test(text)), 'Visible page collection progress');
    await action({ kind: 'searchCancel' });
    const cancelled = await until((state) => !state.pending && state.alerts.some((text) => /Search cancelled/.test(text)), 'Scoped search cancellation');
    assert.equal(cancelled.results.length, 0); assert.deepEqual(cancelled.searchProgress, []);
    evidence.searchCancellation = { progressObserved: collecting.searchProgress, cancelled: true, partialResultsAdvertised: false };
  });
  return { ...evidence, durationMs: Date.now() - started };
}

module.exports = { runUiScenarios };
