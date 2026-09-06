'use strict';

// These functions are serialized into the isolated host document. Keep them
// self-contained: only rendered DOM controls, never MEGA's runtime or APIs.
// Selectors follow meganz/webclient html/js/downloadUI.js and
// js/fm/{megadata/render.js,megadata/menus.js,transfer-progress-widget.js}.
function megaDownloadAction(request = {}, selectCandidate) {
  const attention = (error) => ({ status: 'needs_attention', error });
  if (location.protocol !== 'https:' || !/^(?:www\.)?mega\.(?:nz|co\.nz)$/i.test(location.hostname)) return attention('This is not a supported MEGA page.');
  if (typeof selectCandidate !== 'function' || !document.body) return attention('The MEGA file selection policy is unavailable.');
  const clean = (value, length = 240) => String(value || '').replace(/[\x00-\x1f\x7f]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, length);
  const visible = (element) => {
    if (!element || !element.getClientRects().length) return false;
    for (let node = element; node; node = node.parentElement) {
      if (node.hidden || node.getAttribute('aria-hidden') === 'true' || /(?:^|\s)(?:hidden|vo-hidden)(?:\s|$)/.test(node.getAttribute('class') || '')) return false;
      const style = typeof getComputedStyle === 'function' ? getComputedStyle(node) : node.style;
      if (style && (style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse')) return false;
    }
    return true;
  };
  const all = (selector, scope = document) => Array.from(scope.querySelectorAll(selector)).filter(visible);
  const first = (selector, scope = document) => all(selector, scope)[0];
  const text = (element) => visible(element) ? clean(element.innerText || element.textContent) : '';
  const filename = (element) => {
    if (!visible(element)) return '';
    const name = first('.name', element), ext = first('.ext', element);
    const value = name && ext ? clean(text(name) + text(ext)) : text(element);
    return value.length <= 240 && /\.psarc$/i.test(value) && !/[\\/]/.test(value) ? value : '';
  };
  const body = document.body;
  const idFor = (element) => {
    let id = element.getAttribute('data-feedforge-mega-file');
    if (!/^[a-zA-Z0-9_-]{1,100}$/.test(id || '')) {
      id = 'mega-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 12);
      element.setAttribute('data-feedforge-mega-file', id);
    }
    return id;
  };
  const platform = (label) => /_p\.psarc$/i.test(label) ? 'pc' : /_m\.psarc$/i.test(label) ? 'mac' : 'unknown';
  const owned = () => {
    const label = body.getAttribute('data-feedforge-mega-label') || '';
    const id = body.getAttribute('data-feedforge-mega-id') || '';
    return /\.psarc$/i.test(label) && !/[\\/\x00-\x1f]/.test(label) && /^[a-zA-Z0-9_-]{1,100}$/.test(id) ? { id, label, platform: platform(label) } : null;
  };
  const own = (file, mode) => {
    body.setAttribute('data-feedforge-mega-label', file.label);
    body.setAttribute('data-feedforge-mega-id', file.id);
    body.setAttribute('data-feedforge-mega-mode', mode);
  };
  const controlLabel = (element) => clean([text(element), element.getAttribute('aria-label'), element.getAttribute('data-simpletip'), element.getAttribute('title')].filter(Boolean).join(' '));
  const control = (element) => {
    if (!visible(element) || element.disabled || element.getAttribute('aria-disabled') === 'true' || element.hasAttribute('disabled')) return false;
    const href = element.getAttribute('href');
    // MEGA's normal download controls have handlers, not external URLs.
    return !href || href === '#' || href === 'javascript:void(0)' || href === 'javascript:void(0);';
  };
  const browserLabel = (value) => /(?:download|continue).*(?:in|through|with|using) (?:your |the )?browser|browser download|standard download|ladda (?:ner|ned).*webbläsar/i.test(value);
  const externalLabel = (value) => /mega\s*sync|desktop|(?:get|install|open|launch|with|via|using|in) (?:the )?(?:mega )?app|save to mega|import|zip|folder|entire|all files/i.test(value);
  const browserButtons = all('button, [role="button"], a').filter((button) => control(button) && browserLabel(controlLabel(button)) && !externalLabel(controlLabel(button)));
  const explicitErrors = all('[role="dialog"], [role="alert"], .mega-dialog, .dl-widget .status, .transfer-task-status, .download.error-block, .download.info-block .error, .error-message, .dl-error').map(text).join(' ');
  if (/transfer quota.{0,60}(?:exceed|reached|deplet|limit)|(?:exceed|reached|deplet).{0,60}transfer quota|bandwidth.{0,40}(?:exceed|limit)|over.?quota/i.test(explicitErrors)) return attention('MEGA reports that its transfer quota is exhausted.');
  if (/decryption key|enter.{0,30}(?:key|password)|password.protected|invalid.{0,20}key|missing.{0,20}key/i.test(explicitErrors)
      || all('input[type="password"]').length) return attention('This MEGA link needs a password or decryption key. Open it to continue.');
  if (/(?:file|folder|link).{0,50}(?:no longer available|not available|not found|deleted|removed|expired|taken down|blocked)|(?:file|folder).{0,30}(?:does not exist|doesn.t exist)/i.test(explicitErrors)) return attention('This MEGA file is unavailable or has been removed.');
  if (/download.{0,30}(?:failed|error)|(?:cannot|could not|unable to).{0,30}(?:download|decrypt)|decryption.{0,20}(?:failed|error)/i.test(explicitErrors)) return attention('MEGA could not download or decrypt this file.');

  const active = owned();
  let pendingTransfer = null;
  let transferScope = null;
  const statusOf = (scope, single) => {
    const label = filename(first(single ? '.filename' : '.transfer-name-block .transfer-filetype-txt', scope));
    if (!active || label !== active.label || (!single && scope.getAttribute('zippo') === 'y')) return null;
    const status = text(first(single ? '.status' : '.transfer-task-status', scope));
    if (/mega\s*sync|desktop app|via (?:the )?app/i.test(status)) return attention('MEGA is sending this file to its desktop app. Choose the browser download instead.');
    if (/paused/i.test(status)) return attention('This MEGA download is paused. Open the download page to resume it.');
    const bar = first(single ? '.progress-bar .bar' : '.transfer-progress-bar .transfer-progress-bar-pct', scope);
    const raw = bar && (bar.style?.width || bar.getAttribute('aria-valuenow'));
    const match = String(raw || '').match(/^(\d+(?:\.\d+)?)%?$/) || status.match(/(\d+(?:\.\d+)?)\s*%/);
    const progress = match && Number(match[1]) >= 0 && Number(match[1]) <= 100 ? Number(match[1]) : undefined;
    const phase = /decrypt|dekrypter/i.test(status) ? 'decrypting' : /complete|saving|saved|finished/i.test(status) || progress === 100 ? 'saving' : 'downloading';
    // Only a stable category and visible progress escape the page. Status text
    // can contain service details, so do not return it verbatim.
    const state = /initializ|starting|prepar/i.test(status) ? 'initializing' : /retry|reconnect|waiting/i.test(status) ? 'waiting' : phase;
    return { status: 'transferring', selectedFile: active, phase, ...(progress === undefined ? {} : { progress }), activity: state + ':' + (progress === undefined ? '' : progress) };
  };
  if (active && body.getAttribute('data-feedforge-mega-started') === 'true') {
    const scopes = body.getAttribute('data-feedforge-mega-mode') === 'folder'
      ? all('.transfer-task-row.download').filter((row) => filename(first('.transfer-name-block .transfer-filetype-txt', row)) === active.label)
      : all('.dl-widget.progress').filter((widget) => filename(first('.filename', widget)) === active.label);
    if (scopes.length > 1) return attention('MEGA shows multiple transfers for this filename. Open the page to review them.');
    if (scopes.length === 1) {
      const state = statusOf(scopes[0], body.getAttribute('data-feedforge-mega-mode') !== 'folder');
      if (state) { pendingTransfer = state; transferScope = scopes[0]; }
    }
    if (!browserButtons.length && /mega\s*sync|desktop app|install.{0,30}app|download.{0,30}app/i.test(explicitErrors)) return attention('MEGA is requesting its desktop app. Choose a browser download in the page.');
    // MEGA can render its initializing widget while also waiting for a browser
    // choice. Handle that choice before reporting apparently active progress.
    if (!browserButtons.some((button) => button.getAttribute('data-feedforge-mega-clicked') !== 'true') && pendingTransfer) return pendingTransfer;
  }

  const candidates = [], elements = new Map();
  const headerFiles = all('.dl-header .fileinfo .filename');
  for (const element of headerFiles) {
    const label = filename(element);
    if (label && !candidates.some((candidate) => candidate.label === label)) {
      const candidate = { id: idFor(element), label };
      candidates.push(candidate); elements.set(candidate.id, element);
    }
  }
  const continueHiddenSingle = !candidates.length && active && transferScope && browserButtons.length
    && body.getAttribute('data-feedforge-mega-mode') === 'single';
  if (continueHiddenSingle) {
    candidates.push(active); elements.set(active.id, transferScope);
  }
  const mode = candidates.length ? 'single' : 'folder';
  const rows = mode === 'folder' ? all('.grid-scrolling-table tr, .fm-item') : [];
  if (mode === 'folder') {
    for (const row of rows.slice(0, 500)) {
      if (/(?:^|\s)(?:folder|folder-item)(?:\s|$)/.test(row.getAttribute('class') || '')) continue;
      const name = first('.tranfer-filetype-txt, .transfer-filetype-txt, .file-block-title', row);
      const label = filename(name);
      if (!label) continue;
      const candidate = { id: idFor(row), label };
      candidates.push(candidate); elements.set(candidate.id, row);
    }
  }
  if (!candidates.length) {
    if (all('.dl-header .fileinfo .filename').length) return attention('This MEGA link does not show a PSARC file.');
    if (rows.length) return attention('This MEGA folder does not show an individual PSARC file. Open it to choose a file.');
    return { status: 'waiting' };
  }
  let selection = selectCandidate(candidates, request);
  // Virtualized folder contents cannot establish a unique preferred version.
  // Without an explicit total, ask the user to review the rendered candidates.
  if (mode === 'folder' && !request.choice && selection.status === 'selected') {
    const counts = all('[aria-rowcount]').map((element) => Number(element.getAttribute('aria-rowcount'))).filter((count) => Number.isInteger(count) && count > 0);
    if (!counts.length || Math.max(...counts) > rows.length || rows.length >= 500) {
      selection = { status: 'choose_file', candidates: candidates.map((candidate) => ({ ...candidate, platform: platform(candidate.label) })), error: 'Only part of this MEGA folder may be shown. Choose the individual PC PSARC file to download.' };
    }
  }
  if (selection.status !== 'selected') return selection;
  const selectedFile = selection.candidate;
  if (request.expectedFile && (request.expectedFile.label !== selectedFile.label || request.expectedFile.platform !== selectedFile.platform)) return attention('The MEGA file changed before the download started. Choose it again.');
  if (active && body.getAttribute('data-feedforge-mega-started') === 'true' && (active.label !== selectedFile.label || active.platform !== selectedFile.platform)) return attention('Another MEGA file is already being downloaded in this page.');
  const target = elements.get(selectedFile.id);
  let sizeScope = target;
  if (mode === 'single') {
    for (let node = target; node; node = node.parentElement) {
      if (/(?:^|\s)fileinfo(?:\s|$)/.test(node.getAttribute('class') || '')) { sizeScope = node; break; }
    }
  }
  const sizeText = text(first(mode === 'single' ? '.size' : '.size, .file-size, .file-block-size', sizeScope));
  const sizeMatch = sizeText.match(/^(\d+(?:[.,]\d+)?)\s*(B|bytes?|[KMGT]i?B)$/i);
  let sizeBytes;
  if (sizeMatch) {
    const unit = sizeMatch[2].toUpperCase();
    const power = /^[KMGT]/.test(unit) ? 'KMGT'.indexOf(unit[0]) + 1 : 0;
    // MEGA formats sizes with binary units even when the label says MB.
    sizeBytes = Math.ceil(Number(sizeMatch[1].replace(',', '.')) * 1024 ** power);
  }
  if (continueHiddenSingle) sizeBytes = Number(body.getAttribute('data-feedforge-mega-size'));
  if (!Number.isFinite(sizeBytes) || sizeBytes <= 0) return attention('MEGA does not show a readable file size. Open the file to check it before downloading.');
  const maxBytes = Number.isFinite(request.maxBytes) && request.maxBytes > 0 ? Math.min(request.maxBytes, 512 * 1024 * 1024) : 512 * 1024 * 1024;
  if (sizeBytes > maxBytes) return attention('This MEGA file exceeds the supported download size.');

  const clickDownload = (button) => {
    if (!control(button)) return attention('MEGA does not show an available browser download control.');
    if (button.getAttribute('data-feedforge-mega-clicked') === 'true') return { status: 'already_clicked', selectedFile, sizeBytes };
    if (request.prepareOnly === true) return { status: 'prepared', selectedFile, sizeBytes };
    if (!request.expectedFile || request.expectedFile.label !== selectedFile.label || request.expectedFile.platform !== selectedFile.platform) return attention('The MEGA download must be prepared before it can start.');
    own(selectedFile, mode);
    body.setAttribute('data-feedforge-mega-size', String(sizeBytes));
    // Mark before dispatch: an exception after the event must never replay it.
    button.setAttribute('data-feedforge-mega-clicked', 'true');
    body.setAttribute('data-feedforge-mega-started', 'true');
    button.click();
    return { status: 'clicked', action: 'download', selectedFile, sizeBytes };
  };
  if (mode === 'folder') {
    const menuId = body.getAttribute('data-feedforge-mega-menu-file');
    if (menuId !== selectedFile.id) {
      const arrow = first('.grid-url-arrow, .open-context-menu', target);
      if (!control(arrow)) return attention('Open this individual MEGA file to download it. Its folder menu could not be identified.');
      if (target.getAttribute('data-feedforge-mega-select-clicked') === 'true') return attention('MEGA did not open the selected file menu. Open the file to continue.');
      target.setAttribute('data-feedforge-mega-select-clicked', 'true');
      target.click();
      // MEGA's ordinary context control also selects this row synchronously;
      // some layouts do not select on the row's click event alone.
      arrow.click();
      const selected = rows.filter((row) => /(?:^|\s)ui-selected(?:\s|$)/.test(row.getAttribute('class') || ''));
      if (selected.length !== 1 || selected[0] !== target) return attention('MEGA has not selected exactly one file. Open the folder to choose it.');
      body.setAttribute('data-feedforge-mega-menu-file', selectedFile.id);
      return { status: 'clicked', action: 'select_file' };
    }
    const selected = rows.filter((row) => /(?:^|\s)ui-selected(?:\s|$)/.test(row.getAttribute('class') || ''));
    if (selected.length !== 1 || selected[0] !== target) return attention('The selected MEGA folder file changed. Choose it again.');
    const buttons = all('.download-standart-item').filter((button) => control(button) && !externalLabel(controlLabel(button)));
    if (buttons.length === 1) return clickDownload(buttons[0]);
    if (buttons.length > 1) return attention('MEGA shows several download controls. Open the selected file to continue.');
    return attention('MEGA does not show an individual browser download. Open the selected file to continue.');
  }
  // A rendered browser choice takes precedence over generic Download, which
  // MEGA may otherwise route to its installed desktop client.
  if (browserButtons.length === 1) return clickDownload(browserButtons[0]);
  if (browserButtons.length > 1) return attention('MEGA shows several browser download choices. Open the page to continue.');
  if (body.getAttribute('data-feedforge-mega-started') === 'true') {
    if (/mega\s*sync|desktop app|install.{0,30}app|download.{0,30}app/i.test(explicitErrors)) return attention('MEGA is requesting its desktop app. Choose a browser download in the page.');
    return { status: 'already_clicked', selectedFile, sizeBytes };
  }
  const buttons = all('.dl-header .actions button, .dl-header .actions [role="button"], .download.info-block button, .download.info-block [role="button"]').filter((button) => {
    const label = controlLabel(button);
    return control(button) && !externalLabel(label) && /^(?:download|ladda (?:ner|ned))(?:\s+(?:download|ladda (?:ner|ned)))*$/i.test(label);
  });
  if (buttons.length) return clickDownload(buttons[0]); // Header and footer may duplicate the same file action.
  if (/mega\s*sync|desktop app|install.{0,30}app/i.test(explicitErrors)) return attention('MEGA requires a desktop app for this file. Open the page to review its browser options.');
  return attention('MEGA does not show a supported browser download button. Open the file to continue.');
}

function megaCancelAction() {
  if (location.protocol !== 'https:' || !/^(?:www\.)?mega\.(?:nz|co\.nz)$/i.test(location.hostname) || !document.body) return { status: 'unavailable' };
  const body = document.body;
  const label = body.getAttribute('data-feedforge-mega-label');
  if (!label || body.getAttribute('data-feedforge-mega-started') !== 'true') return { status: 'unavailable' };
  const visible = (element) => {
    if (!element || !element.getClientRects().length) return false;
    for (let node = element; node; node = node.parentElement) {
      if (node.hidden || node.getAttribute('aria-hidden') === 'true' || /(?:^|\s)(?:hidden|vo-hidden)(?:\s|$)/.test(node.getAttribute('class') || '')) return false;
      const style = typeof getComputedStyle === 'function' ? getComputedStyle(node) : node.style;
      if (style && (style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse')) return false;
    }
    return true;
  };
  const all = (selector, scope = document) => Array.from(scope.querySelectorAll(selector)).filter(visible);
  const clean = (value) => String(value || '').replace(/[\x00-\x1f\x7f]/g, ' ').replace(/\s+/g, ' ').trim();
  const fileLabel = (element) => {
    if (!visible(element)) return '';
    const name = all('.name', element)[0], ext = all('.ext', element)[0];
    return name && ext ? clean(name.innerText || name.textContent) + clean(ext.innerText || ext.textContent) : clean(element.innerText || element.textContent);
  };
  const single = body.getAttribute('data-feedforge-mega-mode') !== 'folder';
  const scopes = all(single ? '.dl-widget.progress' : '.transfer-task-row.download').filter((scope) => scope.getAttribute('zippo') !== 'y' && fileLabel(all(single ? '.filename' : '.transfer-name-block .transfer-filetype-txt', scope)[0]) === label);
  if (scopes.length !== 1) return { status: 'unavailable' };
  const buttons = all(single ? '.cols .actions button, .cols .actions [role="button"]' : '.transfer-task-actions button.cancel', scopes[0]).filter((button) => {
    if (button.disabled || button.hasAttribute('disabled') || button.getAttribute('aria-disabled') === 'true') return false;
    const text = clean([button.innerText || button.textContent, button.getAttribute('aria-label'), button.getAttribute('data-simpletip'), button.getAttribute('title')].filter(Boolean).join(' '));
    return !/clear|close|remove|erase/i.test(text) && (/cancel|avbryt/i.test(text) || all('.icon-dialog-close-thin', button).length > 0);
  });
  if (buttons.length !== 1) return { status: 'unavailable' };
  const button = buttons[0];
  if (button.getAttribute('data-feedforge-mega-cancelled') === 'true') return { status: 'already_clicked' };
  button.setAttribute('data-feedforge-mega-cancelled', 'true');
  button.click();
  return { status: 'clicked' };
}

module.exports = { megaDownloadAction, megaCancelAction };
