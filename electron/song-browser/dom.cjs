'use strict';

// These functions are deliberately self-contained: Electron executes their
// toString() output inside the ordinary CustomsForge page. They never fetch an
// endpoint, read cookies, or return the page's signed download links.
function readSearchPage() {
  const empty = (status, error) => ({ status, results: [], hasNext: false, page: null, total: null, ...(error ? { error } : {}) });
  const text = (node) => String(node?.textContent || '').replace(/\s+/g, ' ').trim();
  const visible = (node) => !!node && !node.hidden && node.getAttribute?.('aria-hidden') !== 'true' && node.style?.display !== 'none' && node.style?.visibility !== 'hidden' && (!node.getClientRects || node.getClientRects().length > 0);
  const all = (root, selector) => Array.from(root.querySelectorAll(selector));
  const bodyText = text(document.body);
  const title = String(document.title || '');
  const href = String(globalThis.location?.href || document.URL || '');
  let current;
  try { current = new URL(href); } catch { return empty('layout_changed', 'The song page URL could not be read.'); }

  const challengeFrame = all(document, 'iframe[src], [data-sitekey], #challenge-running, #challenge-stage').some(visible);
  if (/^\s*(just a moment|attention required|security verification|verify (?:that )?you are human)/i.test(title) ||
      (challengeFrame && /(?:verify (?:that )?you are human|checking your browser|complete (?:the )?security check|performing security verification)/i.test(bodyText))) {
    return empty('challenge', 'Complete the website security check in the browser.');
  }
  if (all(document, 'input[type="password"]').some(visible) ||
      (/\/(?:[^/]*oauth[^/]*|login|signin|sign-in)(?:\/|$)/i.test(current.pathname) && /\b(?:sign in|log in|login|authorize)\b/i.test(bodyText))) {
    return empty('login_required', 'Sign in to CustomsForge in the browser.');
  }
  if (current.origin !== 'https://ignition4.customsforge.com') return empty('layout_changed', 'Open the CustomsForge song search page.');
  const table = document.querySelector('#cdlc-table');
  if (!table) {
    // Ignition's observed signed-out landing page stays at the search origin
    // and has no password input. Its visible login prompt is enough to ask
    // the user to sign in; a missing table alone is never treated as login.
    const loginLabels = all(document, 'a, button, h1, h2, h3').filter(visible).map(text);
    if (loginLabels.some((label) => /^login to customsforge$/i.test(label))
      || (loginLabels.some((label) => /^sign in$/i.test(label)) && /\bcustom songs for rocksmith 2014\b/i.test(bodyText))) {
      return empty('login_required', 'Sign in to CustomsForge in the browser.');
    }
    return empty('layout_changed', 'The CustomsForge search table is not available.');
  }
  // A table explicitly marked busy can still contain the previous query's rows
  // or an empty placeholder. Let the adapter keep waiting for a settled render.
  if (table.getAttribute('aria-busy') === 'true') return empty('layout_changed', 'The CustomsForge search results are still loading.');
  const update = document[Symbol.for('feedforge.songBrowser.searchUpdate')];
  if (update?.failed) return empty('layout_changed', 'CustomsForge could not update the results. Run the search again.');
  if (update?.pending) return empty('layout_changed', 'The CustomsForge search results are still loading.');

  const attributes = ['title', 'aria-label', 'data-bs-original-title', 'data-original-title', 'data-tippy-content'];
  const hints = (root) => [root, ...all(root, '[title], [aria-label], [data-bs-original-title], [data-original-title], [data-tippy-content]')]
    .flatMap((node) => attributes.map((name) => node.getAttribute?.(name) || ''));
  const readParts = (root) => {
    if (!root) return { parts: '', arrangements: [] };
    const shown = (node) => {
      for (let ancestor = node; ancestor; ancestor = ancestor.parentElement) {
        if (ancestor.hidden || ancestor.getAttribute?.('aria-hidden') === 'true') return false;
        const style = typeof globalThis.getComputedStyle === 'function' ? globalThis.getComputedStyle(ancestor) : ancestor.style;
        if (style?.display === 'none' || ['hidden', 'collapse'].includes(style?.visibility)
          || style?.contentVisibility === 'hidden' || style?.opacity === '0') return false;
      }
      // display:contents has no own rectangle, although its children render.
      const style = typeof globalThis.getComputedStyle === 'function' ? globalThis.getComputedStyle(node) : node.style;
      return !node.getClientRects || node.getClientRects().length > 0 || style?.display === 'contents';
    };
    const labels = [];
    const visit = (node) => {
      if (!shown(node) || ['SCRIPT', 'STYLE', 'TEMPLATE'].includes(node.tagName)) return;
      for (const child of Array.from(node.childNodes || [])) {
        if (child.nodeType === 3) labels.push(String(child.textContent || ''));
        else if (child.nodeType === 1) visit(child);
      }
      // Ignition keeps absent path badges in hidden DOM branches. Hints on
      // a containing cell must not restore those paths after visibility checks.
      const badge = /(?:^|[\s_-])badge(?:[\s_-]|$)/i.test(String(node.getAttribute?.('class') || ''));
      if (node !== root && (!node.children?.length || badge)) {
        labels.push(...attributes.map((name) => node.getAttribute?.(name) || ''));
      }
    };
    visit(root);
    const found = new Set();
    for (const label of labels) {
      for (const token of label.toLowerCase().match(/[a-z]+/g) || []) {
        if (['lead', 'rhythm', 'bass', 'vocals'].includes(token)) found.add(token);
        else if (/^[lrb]{1,3}$/.test(token) && new Set(token).size === token.length) {
          for (const code of token) found.add({ l: 'lead', r: 'rhythm', b: 'bass' }[code]);
        }
      }
    }
    const arrangements = ['lead', 'rhythm', 'bass'].filter((part) => found.has(part));
    return { parts: [...arrangements, ...(found.has('vocals') ? ['vocals'] : [])].map((part) => part[0].toUpperCase() + part.slice(1)).join(', '), arrangements };
  };
  const officialDlc = (root) => {
    const label = /^(?:ODLC|official\s+DLC)(?:\s*\((?:ODLC|official\s+DLC)\))?[.!]?$/i;
    // This is a catalogue type badge, not a file host. Read only explicit
    // badges/tooltips, never infer official content from its artist or creator.
    return [root, ...all(root, '[class], [title], [aria-label], [data-bs-original-title], [data-original-title], [data-tippy-content]')].some((node) => {
      if (!visible(node)) return false;
      const classes = String(node.getAttribute?.('class') || '').split(/\s+/);
      if (classes.some((name) => /^(?:odlc|official[-_]dlc)$/i.test(name))) return true;
      // A chart title/link can happen to contain the same words; it is not a badge.
      for (let parent = node; parent && parent !== root; parent = parent.parentElement) {
        if (parent.tagName === 'A') return false;
      }
      if (attributes.some((name) => label.test(String(node.getAttribute?.(name) || '').trim()))) return true;
      return classes.some((name) => /(?:^|[-_])badge(?:[-_]|$)/i.test(name)) && label.test(text(node));
    });
  };
  const hostFrom = (root) => {
    if (officialDlc(root)) return 'odlc';
    const found = new Set();
    for (const hint of hints(root)) {
      const match = hint.match(/\bhosted\s+(?:on|by)\s*:?\s*(.+)/i);
      if (!match) continue;
      const name = match[1].trim();
      const domain = name.match(/^(?:https?:\/\/)?((?:[a-z\d-]+\.)+[a-z]{2,})(?=[:/?#\s),;]|$)/i)?.[1].toLowerCase();
      if (domain) {
        if (domain === 'dropbox.com' || domain.endsWith('.dropbox.com') || domain === 'dropboxusercontent.com' || domain.endsWith('.dropboxusercontent.com')) found.add('dropbox');
        else if (['drive.google.com', 'docs.google.com', 'drive.usercontent.google.com'].includes(domain)) found.add('google-drive');
        else if (domain === 'mediafire.com' || domain.endsWith('.mediafire.com')) found.add('mediafire');
        else if (['mega.nz', 'mega.io', 'mega.co.nz', 'www.mega.nz', 'www.mega.io'].includes(domain)) found.add('mega');
        else if (['1drv.ms', 'onedrive.live.com'].includes(domain) || domain.endsWith('.sharepoint.com')) found.add('onedrive');
        else if (domain === 'pcloud.com' || domain.endsWith('.pcloud.com') || domain === 'pcloud.link' || domain.endsWith('.pcloud.link')) found.add('pcloud');
        else found.add('unknown');
      } else if (/^dropbox(?:\s|$)/i.test(name)) found.add('dropbox');
      else if (/^(?:google\s*drive|gdrive)(?:\s|$)/i.test(name)) found.add('google-drive');
      else if (/^media\s*fire(?:\s|$)/i.test(name)) found.add('mediafire');
      else if (/^mega(?:\s|$)/i.test(name)) found.add('mega');
      else if (/^(?:onedrive|sharepoint)(?:\s|$)/i.test(name)) found.add('onedrive');
      else if (/^pcloud(?:\s|$)/i.test(name)) found.add('pcloud');
      else found.add('unknown');
    }
    return found.size === 1 ? [...found][0] : 'unknown';
  };
  const readRecord = (anchor) => {
    try {
      const url = new URL(anchor.getAttribute('href'), current.href);
      const match = url.pathname.match(/^\/cdlc\/([1-9]\d{0,11})\/?$/);
      return url.origin === current.origin && !url.username && !url.password && match ? { id: match[1], url: current.origin + '/cdlc/' + match[1] } : null;
    } catch { return null; }
  };
  const aliases = {
    artist: ['artist', 'band'], title: ['title', 'song', 'songtitle', 'songname'], album: ['album'],
    tuning: ['tuning', 'tunings'], creator: ['creator', 'author', 'charter'],
    parts: ['parts', 'paths', 'arrangements', 'instruments'], version: ['version', 'ver'],
    added: ['added', 'dateadded'], updated: ['updated', 'dateupdated'], year: ['year'], duration: ['duration', 'length'], downloads: ['downloads', 'dls'],
  };
  const headerRows = all(table, 'thead tr').map((row) => Array.from(row.children).filter((cell) => cell.tagName === 'TH' || cell.tagName === 'TD'));
  const headers = headerRows.sort((a, b) => b.length - a.length)[0] || [];
  const mapping = {};
  let semanticHeaders = 0;
  for (const [key, names] of Object.entries(aliases)) {
    const matches = headers.flatMap((cell, index) => names.includes(text(cell).toLowerCase().replace(/[^a-z0-9]/g, '')) ? [index] : []);
    if (matches.length === 1) { mapping[key] = matches[0]; semanticHeaders++; }
    else if (matches.length > 1 && (key === 'title' || key === 'artist')) return empty('layout_changed', 'The search table has ambiguous song columns.');
  }
  const positional = { artist: 1, title: 2, album: 3, tuning: 4, creator: 5, added: 6, updated: 7, parts: 8, version: 9, year: 10, duration: 11, downloads: 12 };
  const rows = all(table, 'tbody > tr').filter(visible);
  const results = [];
  const seen = new Set();
  let unreadable = false;
  let explicitEmpty = false;
  for (const row of rows) {
    const cells = Array.from(row.children).filter((cell) => cell.tagName === 'TD');
    if (cells.length === 1 && cells[0].getAttribute('colspan') && /(?:no (?:matching |available )?(?:results|songs|records|charts)|nothing found)/i.test(text(row))) {
      explicitEmpty = true;
      continue;
    }
    const anchors = all(row, 'a[href]').map((anchor) => ({ anchor, record: readRecord(anchor) })).filter((item) => item.record);
    const ids = new Set(anchors.map((item) => item.record.id));
    if (ids.size !== 1) { if (cells.length > 1) unreadable = true; continue; }
    const indexes = semanticHeaders ? mapping : (cells.length === 13 ? positional : {});
    if (indexes.artist === undefined || indexes.title === undefined) { unreadable = true; continue; }
    const titleCell = cells[indexes.title];
    const titleAnchors = titleCell ? all(titleCell, 'a[href]').filter((anchor) => readRecord(anchor)?.id === anchors[0].record.id) : [];
    const artist = text(cells[indexes.artist]);
    const songTitle = titleAnchors.map(text).find(Boolean);
    if (!artist || !songTitle) { unreadable = true; continue; }
    const record = anchors[0].record;
    if (seen.has(record.id)) continue;
    seen.add(record.id);
    const field = (key) => indexes[key] === undefined ? '' : text(cells[indexes[key]]);
    const partCell = cells[indexes.parts];
    const { parts, arrangements } = readParts(partCell);
    const host = hostFrom(row);
    const number = (value) => /^(?:\d+|\d{1,3}(?:,\d{3})+)$/.test(value) && Number.isSafeInteger(Number(value.replace(/,/g, ''))) ? Number(value.replace(/,/g, '')) : null;
    const date = (key) => {
      const cell = cells[indexes[key]];
      if (!cell) return null;
      const candidates = [field(key), ...all(cell, 'time[datetime]').map((node) => node.getAttribute('datetime')), ...hints(cell)];
      for (const candidate of candidates) {
        const match = String(candidate || '').match(/^(\d{4}-\d{2}-\d{2})(?:T[\d:.]+(?:Z|[+-]\d{2}:\d{2})|(?:\s.*)?)$/);
        if (match && Number.isFinite(Date.parse(match[1])) && new Date(match[1]).toISOString().slice(0, 10) === match[1]) return match[1];
      }
      return null;
    };
    const duration = field('duration').match(/^(?:(\d{1,3}):)?(\d{1,3}):([0-5]\d)$/);
    const durationSeconds = duration && (!duration[1] || Number(duration[2]) < 60) ? Number(duration[1] || 0) * 3600 + Number(duration[2]) * 60 + Number(duration[3]) : null;
    const year = /^\d{4}$/.test(field('year')) ? Number(field('year')) : null;
    const rowHints = hints(row);
    const flag = (name) => rowHints.some((hint) => new RegExp('^(?:this (?:cdlc|chart) (?:is|has been) )?' + name + '(?:[. :]|$)', 'i').test(hint.trim())) ? true : null;
    results.push({ id: record.id, title: songTitle, artist, album: field('album'), tuning: field('tuning'), creator: field('creator'), version: field('version'), parts,
      arrangements,
      downloads: number(field('downloads')), added: date('added'), updated: date('updated'), year, durationSeconds,
      reported: flag('reported'), abandoned: flag('abandoned'),
      host, supported: ['dropbox', 'google-drive', 'mediafire'].includes(host), recordUrl: record.url });
  }
  if (unreadable || (!results.length && !explicitEmpty)) return empty('layout_changed', 'The search results could not be read reliably. Refresh the page or use its browser view.');

  const controls = all(document, 'a, button').filter(visible);
  const hasNext = controls.some((node) => {
    const labels = [node.getAttribute('aria-label'), node.getAttribute('title'), node.getAttribute('data-bs-original-title'), text(node)].filter(Boolean);
    const next = node.getAttribute('rel') === 'next' || labels.some((label) => /^(?:next(?: page)?|go to next page|[›»])$/i.test(label.trim()));
    const disabled = node.disabled || node.hasAttribute?.('disabled') || node.getAttribute('aria-disabled') === 'true' || node.parentElement?.getAttribute('aria-disabled') === 'true' || /(?:^|\s)disabled(?:\s|$)/.test(node.className || '') || /(?:^|\s)disabled(?:\s|$)/.test(node.parentElement?.className || '');
    return next && !disabled;
  });
  const selectedPage = all(document, '[aria-current="page"]').find((node) => visible(node) && /^\d+$/.test(text(node)));
  const pageInput = all(document, 'input[aria-label="Go to page"]').find(visible);
  const inputValue = String(pageInput?.value || pageInput?.getAttribute('value') || '');
  const pageMatch = bodyText.match(/\bpage\s+(\d+)\s+of\s+\d+/i);
  const pageValue = /^[1-9]\d*$/.test(inputValue) ? inputValue : (selectedPage ? text(selectedPage) : pageMatch?.[1]);
  const countMatch = bodyText.match(/\bshowing\s+[\d,]+\s+(?:to|[-–])\s+[\d,]+\s+of\s+([\d,]+)\s+(?:results?|charts?|songs?|records?)\b/i) || bodyText.match(/\b([\d,]+)\s+(?:results?|charts?|songs?)\b/i);
  const sortKeys = ['artist', 'title', 'album', 'tuning', 'creator', 'added', 'updated', 'year', 'duration', 'downloads'];
  const sorts = sortKeys.flatMap((key) => {
    const cell = headers[mapping[key]];
    if (!cell) return [];
    const values = [...new Set([cell, ...all(cell, '[aria-sort]')].map((node) => node.getAttribute('aria-sort')).filter((value) => ['ascending', 'descending'].includes(value)))];
    return values.length === 1 ? [{ field: key, direction: values[0] === 'ascending' ? 'asc' : 'desc' }] : [];
  });
  return { status: 'ready', results, hasNext, page: pageValue ? Number(pageValue) : null, total: countMatch ? Number(countMatch[1].replace(/,/g, '')) : (explicitEmpty && !results.length ? 0 : null), ...(sorts.length === 1 ? { sort: sorts[0] } : {}) };
}

function requestChartDownload(request) {
  const reply = (status, host = 'unknown', error) => ({ status, host, ...(error ? { error } : {}) });
  const id = String(request?.id ?? '');
  if (!/^[1-9]\d{0,11}$/.test(id)) return reply('invalid_request', 'unknown', 'Select a valid CustomsForge chart.');
  const all = (root, selector) => Array.from(root.querySelectorAll(selector));
  const text = (node) => String(node?.textContent || '').replace(/\s+/g, ' ').trim();
  const visible = (node) => !!node && !node.hidden && node.getAttribute?.('aria-hidden') !== 'true' && node.style?.display !== 'none' && node.style?.visibility !== 'hidden' && (!node.getClientRects || node.getClientRects().length > 0);
  const bodyText = text(document.body);
  let current;
  try { current = new URL(String(globalThis.location?.href || document.URL || '')); } catch { return reply('layout_changed', 'unknown', 'The chart page URL could not be read.'); }
  const challengeFrame = all(document, 'iframe[src], [data-sitekey], #challenge-running, #challenge-stage').some(visible);
  if (/^\s*(just a moment|attention required|security verification|verify (?:that )?you are human)/i.test(String(document.title || '')) ||
      (challengeFrame && /(?:verify (?:that )?you are human|checking your browser|complete (?:the )?security check|performing security verification)/i.test(bodyText))) {
    return reply('challenge', 'unknown', 'Complete the website security check in the browser.');
  }
  if (all(document, 'input[type="password"]').some(visible) ||
      (/\/(?:[^/]*oauth[^/]*|login|signin|sign-in)(?:\/|$)/i.test(current.pathname) && /\b(?:sign in|log in|login|authorize)\b/i.test(bodyText))) {
    return reply('login_required', 'unknown', 'Sign in to CustomsForge in the browser.');
  }
  if (current.origin !== 'https://ignition4.customsforge.com' || !new RegExp('^/cdlc/' + id + '/?$').test(current.pathname)) {
    return reply('layout_changed', 'unknown', 'Open the selected chart page before downloading.');
  }
  const marker = Symbol.for('feedforge.songBrowser.downloadClicks');
  if (document[marker]?.has(id)) return reply('already_clicked', document[marker].get(id), 'The download was already requested on this page.');
  const attributes = ['title', 'aria-label', 'data-bs-original-title', 'data-original-title', 'data-tippy-content'];
  const hostFrom = (root) => {
    const found = new Set();
    for (const node of [root, ...all(root, '[title], [aria-label], [data-bs-original-title], [data-original-title], [data-tippy-content]')]) {
      for (const attribute of attributes) {
        const match = String(node.getAttribute?.(attribute) || '').match(/\bhosted\s+(?:on|by)\s*:?\s*(.+)/i);
        if (!match) continue;
        const name = match[1].trim();
        const domain = name.match(/^(?:https?:\/\/)?((?:[a-z\d-]+\.)+[a-z]{2,})(?=[:/?#\s),;]|$)/i)?.[1].toLowerCase();
        if (domain) {
          if (domain === 'dropbox.com' || domain.endsWith('.dropbox.com') || domain === 'dropboxusercontent.com' || domain.endsWith('.dropboxusercontent.com')) found.add('dropbox');
          else if (['drive.google.com', 'docs.google.com', 'drive.usercontent.google.com'].includes(domain)) found.add('google-drive');
          else if (domain === 'mediafire.com' || domain.endsWith('.mediafire.com')) found.add('mediafire');
          else if (['mega.nz', 'mega.io', 'mega.co.nz', 'www.mega.nz', 'www.mega.io'].includes(domain)) found.add('mega');
          else if (['1drv.ms', 'onedrive.live.com'].includes(domain) || domain.endsWith('.sharepoint.com')) found.add('onedrive');
          else if (domain === 'pcloud.com' || domain.endsWith('.pcloud.com') || domain === 'pcloud.link' || domain.endsWith('.pcloud.link')) found.add('pcloud');
          else found.add('unknown');
        } else if (/^dropbox(?:\s|$)/i.test(name)) found.add('dropbox');
        else if (/^(?:google\s*drive|gdrive)(?:\s|$)/i.test(name)) found.add('google-drive');
        else if (/^media\s*fire(?:\s|$)/i.test(name)) found.add('mediafire');
        else if (/^mega(?:\s|$)/i.test(name)) found.add('mega');
        else if (/^(?:onedrive|sharepoint)(?:\s|$)/i.test(name)) found.add('onedrive');
        else if (/^pcloud(?:\s|$)/i.test(name)) found.add('pcloud');
        else found.add('unknown');
      }
    }
    return found.size === 1 ? [...found][0] : 'unknown';
  };
  const candidates = [];
  for (const anchor of all(document, 'a[href]').filter(visible)) {
    let url;
    try { url = new URL(anchor.getAttribute('href'), current.href); } catch { continue; }
    if (url.origin !== current.origin || url.username || url.password || url.pathname !== '/user/collectedcdlcs/toggle/' + id) continue;
    if (url.searchParams.getAll('platform').length !== 1 || url.searchParams.get('platform') !== 'pc') continue;
    if (anchor.getAttribute('aria-disabled') === 'true' || anchor.hasAttribute('disabled')) continue;
    if (url.searchParams.getAll('expires').length !== 1 || url.searchParams.getAll('signature').length !== 1) continue;
    const expires = url.searchParams.get('expires');
    const signature = url.searchParams.get('signature');
    if (!/^\d+$/.test(expires) || !Number.isSafeInteger(Number(expires)) || !signature || signature.length > 512) continue;
    candidates.push({ anchor, url, expires: Number(expires) });
  }
  const unique = [...new Map(candidates.map((candidate) => [candidate.url.href, candidate])).values()];
  if (unique.length !== 1) return reply('layout_changed', 'unknown', 'A single valid Windows download button could not be identified.');
  const candidate = unique[0];
  let host = hostFrom(candidate.anchor);
  let ancestor = candidate.anchor.parentElement;
  for (let depth = 0; host === 'unknown' && ancestor && depth < 2; depth++, ancestor = ancestor.parentElement) host = hostFrom(ancestor);
  if (candidate.expires <= Math.floor(Date.now() / 1000) + 10) return reply('expired', host, 'The download link expired. Reload the chart page to obtain a fresh button.');
  const knownHosts = ['dropbox', 'google-drive', 'mediafire', 'onedrive', 'mega', 'pcloud'];
  const supportedHosts = Array.isArray(request?.supportedHosts) ? request.supportedHosts.filter((value) => knownHosts.includes(value)) : ['dropbox', 'google-drive', 'mediafire'];
  if (!supportedHosts.includes(host)) return reply('unsupported', host, 'This host is not supported for automatic downloads.');

  // Mark before clicking: the collection route has a side effect, so an
  // ambiguous event must not trigger a second click in this document.
  if (!document[marker]) Object.defineProperty(document, marker, { value: new Map() });
  document[marker].set(id, host);
  try { candidate.anchor.click(); } catch { return reply('click_failed', host, 'The browser could not activate the download button. Check the browser before retrying.'); }
  return reply('clicked', host);
}

// Ignition predicts aria-sort before its Livewire response updates the rows.
// Observe public lifecycle hooks, scoped to the catalogue component, without
// reading request payloads, snapshots, account data or framework state.
function prepareSearchUpdates() {
  if (globalThis.location?.origin !== 'https://ignition4.customsforge.com') return { status: 'layout_changed', error: 'The song search page is not available.' };
  const key = Symbol.for('feedforge.songBrowser.searchUpdate');
  if (document[key]) return { status: 'ready' };
  if (!document.querySelector('[data-cdlc-table]')) return { status: 'ready' };
  if (typeof globalThis.Livewire?.hook !== 'function') return { status: 'waiting', error: 'The search controls are still loading.' };
  const state = { pending: false, failed: false, generation: 0, commits: new Set() };
  const matches = (component) => {
    const table = document.querySelector('#cdlc-table');
    return !!table && (component?.el === table || component?.el?.contains?.(table));
  };
  const finish = () => {
    if (state.pending && state.commits.size && [...state.commits].every((commit) => commit.succeeded && commit.morphed)) {
      state.pending = false; state.commits.clear();
    }
  };
  state.arm = () => { state.generation++; state.pending = true; state.failed = false; state.commits.clear(); };
  globalThis.Livewire.hook('commit', ({ component, succeed, fail }) => {
    if (!state.pending || !matches(component)) return;
    const generation = state.generation;
    const commit = { component, succeeded: false, morphed: false };
    state.commits.add(commit);
    succeed(() => { if (state.generation === generation) { commit.succeeded = true; finish(); } });
    fail(() => { if (state.generation === generation) { state.failed = true; state.pending = false; state.commits.clear(); } });
  });
  globalThis.Livewire.hook('morphed', ({ component }) => {
    if (!state.pending) return;
    for (const commit of state.commits) if (commit.component === component) commit.morphed = true;
    finish();
  });
  Object.defineProperty(document, key, { value: state });
  return { status: 'ready' };
}

function requestSearchPage(request) {
  const direction = request?.direction;
  if (direction !== 'next' && direction !== 'previous') return { status: 'layout_changed', error: 'Select a valid page direction.' };
  let current;
  try { current = new URL(String(globalThis.location?.href || document.URL || '')); } catch { return { status: 'layout_changed', error: 'The song page URL could not be read.' }; }
  if (current.origin !== 'https://ignition4.customsforge.com' || !document.querySelector('#cdlc-table')) return { status: 'layout_changed', error: 'The song search page is not available.' };
  const update = document[Symbol.for('feedforge.songBrowser.searchUpdate')];
  if (update?.failed) return { status: 'layout_changed', error: 'CustomsForge could not update the results. Run the search again.' };
  if (update?.pending) return { status: 'waiting' };
  const text = (node) => String(node?.textContent || '').replace(/\s+/g, ' ').trim();
  const matches = direction === 'next' ? /^(?:next(?: page)?|go to next page|[›»])$/i : /^(?:prev(?:ious)?(?: page)?|go to previous page|[‹«])$/i;
  for (const node of Array.from(document.querySelectorAll('a, button'))) {
    if (node.hidden || node.getAttribute('aria-hidden') === 'true' || node.style?.display === 'none' || node.style?.visibility === 'hidden' || (node.getClientRects && !node.getClientRects().length)) continue;
    if (node.disabled || node.hasAttribute('disabled') || node.getAttribute('aria-disabled') === 'true' || node.parentElement?.getAttribute('aria-disabled') === 'true' || /(?:^|\s)disabled(?:\s|$)/.test(node.className || '') || /(?:^|\s)disabled(?:\s|$)/.test(node.parentElement?.className || '')) continue;
    const labels = [node.getAttribute('aria-label'), node.getAttribute('title'), node.getAttribute('data-bs-original-title'), text(node)].filter(Boolean);
    const rel = node.getAttribute('rel');
    if (!labels.some((label) => matches.test(label.trim())) && rel !== (direction === 'next' ? 'next' : 'prev')) continue;
    if (node.tagName === 'A') {
      try { const url = new URL(node.getAttribute('href'), current.href); if (url.origin !== current.origin || url.username || url.password) continue; } catch { continue; }
    }
    update?.arm();
    try { node.click(); } catch { if (update) { update.pending = false; update.failed = true; } return { status: 'layout_changed', error: 'The browser could not activate the page button.' }; }
    return { status: 'clicked' };
  }
  return { status: 'layout_changed', error: 'The requested page button is unavailable.' };
}

// Toggle only the observed table control. The adapter waits for the table to
// settle after each click and calls again to verify; this function never guesses
// the website's state or cycles controls in a single injected execution.
function requestSearchSort(request) {
  const labels = { artist: ['Artist'], title: ['Title'], album: ['Album'], tuning: ['Tuning'], creator: ['Creator'], added: ['Added'], updated: ['Updated'], year: ['Year'], duration: ['Duration'], downloads: ['DLs', 'Downloads'] };
  const field = request?.field, direction = request?.direction;
  const failed = (error) => ({ status: 'layout_changed', error });
  if (!Object.hasOwn(labels, field) || !['asc', 'desc'].includes(direction)) return failed('Choose a valid search order.');
  let url;
  try { url = new URL(String(globalThis.location?.href || document.URL || '')); } catch { return failed('The song page URL could not be read.'); }
  const table = document.querySelector('#cdlc-table');
  if (url.origin !== 'https://ignition4.customsforge.com' || !table) return failed('The song search page is not available.');
  if (table.getAttribute('aria-busy') === 'true') return { status: 'waiting', error: 'The search results are still loading.' };
  const update = document[Symbol.for('feedforge.songBrowser.searchUpdate')];
  if (update?.failed) return failed('CustomsForge could not update the results. Run the search again.');
  if (update?.pending) return { status: 'waiting' };
  const text = (node) => String(node?.textContent || '').replace(/\s+/g, ' ').trim();
  const visible = (node) => !!node && !node.hidden && node.getAttribute?.('aria-hidden') !== 'true' && node.style?.display !== 'none' && node.style?.visibility !== 'hidden' && (!node.getClientRects || node.getClientRects().length > 0);
  // Ignition includes an aria-hidden ▲/▼ indicator even on unsorted columns.
  // textContent includes that hidden glyph. Use the same semantic normalization
  // as readSearchPage so reading and activating a column agree.
  const headerName = (node) => text(node).toLowerCase().replace(/[^a-z0-9]/g, '');
  const headings = Array.from(table.querySelectorAll('thead th')).filter((cell) => visible(cell) && labels[field].some((label) => headerName(cell) === label.toLowerCase()));
  if (headings.length !== 1) return failed('The requested sort column could not be identified reliably.');
  const heading = headings[0];
  const controls = Array.from(heading.querySelectorAll('button, [role="button"]')).filter((node) => visible(node) && !node.disabled && !node.hasAttribute('disabled') && node.getAttribute('aria-disabled') !== 'true');
  if (controls.length !== 1) return failed('The requested sort button is unavailable.');
  const values = [heading, ...Array.from(heading.querySelectorAll('[aria-sort]'))].map((node) => node.getAttribute('aria-sort')).filter((value) => ['ascending', 'descending'].includes(value));
  if (new Set(values).size > 1) return failed('The current search order is ambiguous.');
  const observed = values.length ? { field, direction: values[0] === 'ascending' ? 'asc' : 'desc' } : null;
  if (observed?.direction === direction) return { status: 'applied', sort: observed };
  update?.arm();
  try { controls[0].click(); } catch { if (update) { update.pending = false; update.failed = true; } return failed('The browser could not activate the sort button.'); }
  return { status: 'clicked', sort: observed };
}

module.exports = { readSearchPage, requestChartDownload, requestSearchPage, requestSearchSort, prepareSearchUpdates };
