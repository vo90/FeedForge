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

  const attributes = ['title', 'aria-label', 'data-bs-original-title', 'data-original-title', 'data-tippy-content'];
  const hints = (root) => [root, ...all(root, '[title], [aria-label], [data-bs-original-title], [data-original-title], [data-tippy-content]')]
    .flatMap((node) => attributes.map((name) => node.getAttribute?.(name) || ''));
  const hostFrom = (root) => {
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
  const positional = { artist: 1, title: 2, album: 3, tuning: 4, creator: 5, parts: 8, version: 9 };
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
    const parts = field('parts') || (partCell ? [...new Set(hints(partCell).filter(Boolean))].join(', ') : '');
    const host = hostFrom(row);
    results.push({ id: record.id, title: songTitle, artist, album: field('album'), tuning: field('tuning'), creator: field('creator'), version: field('version'), parts, host, supported: ['dropbox', 'google-drive', 'mediafire'].includes(host), recordUrl: record.url });
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
  const countMatch = bodyText.match(/\bshowing\s+[\d,]+\s+(?:to|[-–])\s+[\d,]+\s+of\s+([\d,]+)\s+(?:results|charts|songs|records)\b/i) || bodyText.match(/\b([\d,]+)\s+(?:results|charts|songs)\b/i);
  return { status: 'ready', results, hasNext, page: pageValue ? Number(pageValue) : null, total: countMatch ? Number(countMatch[1].replace(/,/g, '')) : (explicitEmpty && !results.length ? 0 : null) };
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
  if (!['dropbox', 'google-drive', 'mediafire'].includes(host)) return reply('unsupported', host, 'This host is not supported by the prototype.');

  // Mark before clicking: the collection route has a side effect, so an
  // ambiguous event must not trigger a second click in this document.
  if (!document[marker]) Object.defineProperty(document, marker, { value: new Map() });
  document[marker].set(id, host);
  try { candidate.anchor.click(); } catch { return reply('click_failed', host, 'The browser could not activate the download button. Check the browser before retrying.'); }
  return reply('clicked', host);
}

function requestSearchPage(request) {
  const direction = request?.direction;
  if (direction !== 'next' && direction !== 'previous') return { status: 'layout_changed', error: 'Select a valid page direction.' };
  let current;
  try { current = new URL(String(globalThis.location?.href || document.URL || '')); } catch { return { status: 'layout_changed', error: 'The song page URL could not be read.' }; }
  if (current.origin !== 'https://ignition4.customsforge.com' || !document.querySelector('#cdlc-table')) return { status: 'layout_changed', error: 'The song search page is not available.' };
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
    try { node.click(); } catch { return { status: 'layout_changed', error: 'The browser could not activate the page button.' }; }
    return { status: 'clicked' };
  }
  return { status: 'layout_changed', error: 'The requested page button is unavailable.' };
}

module.exports = { readSearchPage, requestChartDownload, requestSearchPage };
