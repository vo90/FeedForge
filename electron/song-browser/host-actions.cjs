'use strict';
const { selectFileCandidate } = require('./file-selection.cjs');
const { megaDownloadAction, megaCancelAction } = require('./mega-actions.cjs');

// Runs in the sandboxed host document: visible DOM and ordinary page controls
// only. No network APIs, credentials, or host application runtime are inspected.
function hostDownloadAction(request = {}, selectCandidate, megaAction) {
  const host = location.hostname;
  // MEGA embeds hidden error/dialog templates; its adapter checks rendered UI.
  if (['mega.nz', 'www.mega.nz', 'mega.co.nz', 'www.mega.co.nz'].includes(host)) {
    return typeof megaAction === 'function' ? megaAction(request, selectCandidate) : { status: 'waiting' };
  }
  const text = (document.body?.innerText || '').slice(0, 30000);
  if (/verify you are human|checking your browser|captcha/i.test(document.title + '\n' + text)) {
    return { status: 'challenge', error: 'Complete the host check in the browser.' };
  }
  if (/too many users have viewed or downloaded|download quota|transfer quota|file you have requested does not exist|file has been deleted|you need access|request access|download(?:ing|s)? (?:has been |is |are )?(?:disabled|blocked)|link (?:has )?expired/i.test(text)) {
    return { status: 'needs_attention', error: 'The file is restricted, unavailable, or has reached its download limit. Check the browser.' };
  }
  if (host === 'accounts.google.com') return { status: 'login_required', error: 'This file needs a Google sign-in. Continue in the browser.' };
  if (host === 'login.live.com' || host === 'login.microsoftonline.com') return { status: 'login_required', error: 'This file needs a Microsoft sign-in. Continue in the browser.' };
  const usable = (element) => {
    if (!element || element.disabled || element.hasAttribute('disabled') || !element.getClientRects().length) return false;
    for (let node = element; node; node = node.parentElement) {
      if (node.hidden || node.getAttribute('aria-hidden') === 'true' || node.getAttribute('aria-disabled') === 'true'
        || node.style?.display === 'none' || node.style?.visibility === 'hidden') return false;
      if (typeof getComputedStyle === 'function') {
        const style = getComputedStyle(node);
        if (style.display === 'none' || style.visibility === 'hidden') return false;
      }
    }
    return true;
  };
  const marker = 'data-feedforge-download-requested';
  const click = (element, double = false) => {
    if (element.hasAttribute(marker)) return { status: 'already_clicked' };
    element.setAttribute(marker, 'true');
    try {
      element.click();
      if (double) element.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, cancelable: true, view: window }));
      return { status: 'clicked' };
    } catch { return { status: 'needs_attention', error: 'The file could not be opened. Review the host page before retrying.' }; }
  };
  const secureTarget = (value) => {
    try {
      const target = new URL(value, location.href);
      return target.protocol === 'https:' && !target.username && !target.password && (!target.port || target.port === '443') ? target : null;
    } catch { return null; }
  };
  const inDomain = (name, domain) => name === domain || name.endsWith('.' + domain);
  const isDropbox = ['www.dropbox.com', 'dropbox.com'].includes(host);
  const isMediaFire = ['www.mediafire.com', 'mediafire.com'].includes(host);
  const isDrive = ['drive.google.com', 'docs.google.com', 'drive.usercontent.google.com'].includes(host);
  const isOneDrive = ['1drv.ms', 'onedrive.live.com'].includes(host) || inDomain(host, 'sharepoint.com');
  const isPcloud = inDomain(host, 'pcloud.com') || inDomain(host, 'pcloud.link');
  if (!isDropbox && !isMediaFire && !isDrive && !isOneDrive && !isPcloud) return { status: 'waiting' };
  const ownFileTarget = (target) => target && (
    (isDropbox && ['www.dropbox.com', 'dropbox.com'].includes(target.hostname) && /^\/(?:s\/|scl\/fi\/)/.test(target.pathname))
    || (isDrive && ['drive.google.com', 'docs.google.com'].includes(target.hostname) && /^\/file\/d\/[^/]+/.test(target.pathname))
    || (isMediaFire && ['mediafire.com', 'www.mediafire.com'].includes(target.hostname) && /^\/file\//.test(target.pathname))
    || (isOneDrive && (['1drv.ms', 'onedrive.live.com'].includes(target.hostname) || inDomain(target.hostname, 'sharepoint.com')))
    || (isPcloud && (inDomain(target.hostname, 'pcloud.com') || inDomain(target.hostname, 'pcloud.link'))));
  const clean = (value) => typeof value === 'string' ? value.replace(/[\x00-\x1f\x7f]/g, '').trim().slice(0, 240) : '';
  const filename = (element) => {
    const values = ['data-filename', 'data-name', 'title', 'aria-label'].map((key) => element.getAttribute(key));
    values.push(...String(element.innerText || element.textContent || '').split(/\r?\n/));
    for (const value of values) {
      const label = clean(value).replace(/^(?:file(?: name)?|name):\s*/i, '');
      if (/\.psarc$/i.test(label) && !/[\\/]/.test(label)) return label;
    }
    return '';
  };
  const chooseVisibleFile = () => {
    if (typeof selectCandidate !== 'function') return null;
    const nodes = [...document.querySelectorAll('a, [role="row"], [role="option"], [role="listitem"], [data-filename]')].filter(usable).slice(0, 1500);
    const records = [], targets = new Set();
    for (const node of nodes) {
      const label = filename(node);
      if (!label) continue;
      const link = String(node.tagName || '').toLowerCase() === 'a' ? node : node.querySelector?.('a[href]');
      const target = link ? secureTarget(link.href || link.getAttribute('href')) : null;
      const role = node.getAttribute('role');
      // Name and thumbnail links may identify the same file. Two different
      // destinations with the same filename still require an explicit choice.
      if (target && ownFileTarget(target)) {
        if (targets.has(target.href)) continue;
        targets.add(target.href);
      } else if (link || !['row', 'option', 'listitem'].includes(role) || !(isDrive || isOneDrive || isPcloud)) continue;
      if (records.some((row) => row.node === node || row.node.contains?.(node) || node.contains?.(row.node))) continue;
      let id = node.getAttribute('data-feedforge-file-id');
      if (!/^[a-zA-Z0-9_-]{1,100}$/.test(id || '') || records.some((row) => row.id === id)) {
        id = 'ff-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 12);
        node.setAttribute('data-feedforge-file-id', id);
      }
      records.push({ id, label, node, action: target && ownFileTarget(target) ? link : node, double: !(target && ownFileTarget(target)) });
    }
    if (!records.length) return null;
    const selection = selectCandidate(records.map(({ id, label }) => ({ id, label })), request);
    const totalRows = [...document.querySelectorAll('[aria-rowcount]')].filter(usable)
      .map((element) => Number(element.getAttribute('aria-rowcount'))).filter((count) => Number.isInteger(count) && count > 0);
    if (!request.choice && selection.status === 'selected' && (records.length > 500 || totalRows.some((count) => count > records.length))) {
      return { status: 'choose_file', candidates: records.slice(0, 500).map(({ id, label }) => ({ id, label, platform: /_p\.psarc$/i.test(label) ? 'pc' : /_m\.psarc$/i.test(label) ? 'mac' : 'unknown' })),
        error: 'Only part of this folder is visible. Choose a displayed PSARC or open the browser to inspect the rest.' };
    }
    if (selection.status !== 'selected') return selection;
    const record = records.find((row) => row.id === selection.candidate.id);
    if (!record) return { status: 'needs_attention', error: 'The file list changed. Choose the file again.' };
    return { ...click(record.action, record.double), selectedFile: selection.candidate };
  };
  const pathname = new URL(location.href).pathname;
  const onFolder = (isDropbox && /^\/(?:scl\/fo\/|sh\/)/.test(pathname))
    || (isDrive && /\/folders\//.test(pathname)) || isOneDrive || isPcloud || (isMediaFire && /^\/folder\//.test(pathname));
  if (onFolder) {
    const file = chooseVisibleFile();
    if (file) return file;
    if (request.choice) return { status: 'choose_file', candidates: [], error: 'The selected PSARC is not visible. Open its folder and choose the file again.' };
  }
  if (host === 'www.dropbox.com' || host === 'dropbox.com') {
    const url = new URL(location.href);
    let name = '';
    try { name = decodeURIComponent(url.pathname.split('/').at(-1)); } catch { /* Contents are checked later. */ }
    if (/_m\.psarc$/i.test(name) && request.allowMacFallback !== true) return { status: 'needs_attention', error: 'This link points to a Mac PSARC. Choose its PC version.' };
    // Only individual shared files; never turn an arbitrary/folder URL into a download.
    if (/^\/(?:s\/[\w-]+\/|scl\/fi\/)/.test(url.pathname) && url.searchParams.get('dl') !== '1') {
      url.searchParams.set('dl', '1');
      location.assign(url.href);
      return { status: 'clicked' };
    }
    return { status: 'needs_attention', error: 'Select the individual PSARC file in the Dropbox page.' };
  }
  if (host === 'www.mediafire.com' || host === 'mediafire.com') {
    const link = document.querySelector('#downloadButton');
    if (usable(link)) {
      try {
        const target = new URL(link.href, location.href);
        if (/_m\.psarc$/i.test(target.pathname) && request.allowMacFallback !== true) return { status: 'needs_attention', error: 'This link points to a Mac PSARC. Choose its PC version.' };
        if (target.protocol === 'https:' && !target.username && !target.password && (!target.port || target.port === '443')
          && /^download\d+\.mediafire\.com$/.test(target.hostname)) return click(link);
      } catch { /* Let the user inspect an unfamiliar page. */ }
    }
    return { status: 'needs_attention', error: 'Use the file download button on MediaFire to continue.' };
  }
  if (isDrive) {
    if (/\/folders\//.test(pathname)) return { status: 'needs_attention', error: 'Open the individual PSARC in the Google Drive folder.' };
    if (/_m\.psarc(?:\s|$)/i.test(document.title || '') && request.allowMacFallback !== true) return { status: 'needs_attention', error: 'This is a Mac PSARC. Choose its PC version.' };
    const buttons = [...document.querySelectorAll('button, [role="button"], a, input[type="submit"]')];
    const download = buttons.find((el) => {
      const label = (el.getAttribute('aria-label') || el.getAttribute('data-tooltip') || el.innerText || el.value || '').trim();
      return /^(?:download|download anyway|ladda ned)$/i.test(label) && usable(el);
    });
    if (download) return click(download);
    return { status: 'waiting' };
  }
  if (isOneDrive || isPcloud) {
    // A folder toolbar Download can create a ZIP. Require a visible individual
    // filename heading before clicking, otherwise let the user choose a file.
    const names = [...new Set([...document.querySelectorAll('h1, h2, [role="heading"]')].filter(usable).map(filename).filter(Boolean))];
    if (names.length === 1) {
      if (/_m\.psarc$/i.test(names[0]) && request.allowMacFallback !== true) return { status: 'needs_attention', error: 'This is a Mac PSARC. Choose its PC version.' };
      const download = [...document.querySelectorAll('button, [role="button"], a, input[type="submit"]')].find((element) => {
        const label = (element.getAttribute('aria-label') || element.getAttribute('data-tooltip') || element.innerText || element.value || '').trim();
        return /^(?:download|download anyway|ladda ned|hämta)$/i.test(label) && usable(element);
      });
      if (download) return click(download);
    }
    return { status: 'needs_attention', error: 'Open an individual PSARC file, then use its download button.' };
  }
  return { status: 'waiting' };
}
function hostActionScript(request = {}) {
  return `(${hostDownloadAction.toString()})(${JSON.stringify(request)}, ${selectFileCandidate.toString()}, ${megaDownloadAction.toString()})`;
}
function hostCancelScript() { return `(${megaCancelAction.toString()})()`; }
module.exports = { hostDownloadAction, hostActionScript, hostCancelScript };
