'use strict';

// Serialized into an ordinary sandboxed page. Only rendered content and
// ordinary controls are read: no cookies, page stores or private APIs.
function readSongsterrPage() {
  const text = (node) => String(node?.textContent || '').replace(/\s+/g, ' ').trim();
  const all = (root, selector) => Array.from(root.querySelectorAll(selector));
  const visible = (node) => {
    if (!node) return false;
    for (let at = node; at; at = at.parentElement) {
      if (at.hidden || at.getAttribute?.('aria-hidden') === 'true' || at.style?.display === 'none' || at.style?.visibility === 'hidden') return false;
    }
    return !node.getClientRects || node.getClientRects().length > 0;
  };
  const label = (node) => String(node?.getAttribute?.('aria-label') || node?.getAttribute?.('title') || text(node));
  const href = String(globalThis.location?.href || document.URL || '');
  let current;
  try { current = new URL(href); } catch { return { status: 'unavailable', reason: 'invalid_page' }; }
  if (current.origin !== 'https://www.songsterr.com') return { status: 'unavailable', reason: 'wrong_origin' };
  const body = text(document.body), pageTitle = String(document.title || '');
  if (/^(?:Just a moment|Attention required)/i.test(pageTitle) || /verify (?:that )?you are human|checking your browser/i.test(body)) return { status: 'needs_attention', reason: 'challenge' };
  const loginRequired = all(document, 'input[type="password"]').some(visible) || /please sign (?:up|in).*create and edit a copy/i.test(body);
  const controls = all(document, 'a, button').filter(visible);
  const signedOut = controls.some((node) => /^sign in$/i.test(label(node)));
  const songMatch = /-s([1-9]\d{0,11})(?:t\d+)?(?:\/|$)/.exec(current.pathname);
  const revisionsInPath = [...current.pathname.matchAll(/r([1-9]\d{0,11})/g)];
  const currentRevision = revisionsInPath.at(-1)?.[1] || null;
  const results = [], seen = new Set();
  for (const anchor of all(document, 'a[href]').filter(visible)) {
    let url;
    try { url = new URL(anchor.getAttribute('href'), current); } catch { continue; }
    const match = /^\/a\/wsa\/[^/]+-s([1-9]\d{0,11})(?:t\d+)?$/.exec(url.pathname);
    if (!match || url.origin !== current.origin || seen.has(match[1])) continue;
    const titleNode = anchor.querySelector('[data-testid="song-title"], [data-testid="title"], [class*="song-title"], [class*="SongTitle"]');
    const artistNode = anchor.querySelector('[data-testid="artist"], [data-testid="song-artist"], [class*="song-artist"], [class*="Artist"]');
    const lines = String(anchor.innerText || '').split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
    const children = Array.from(anchor.children || []).filter(visible).map(text).filter(Boolean);
    const title = text(titleNode) || anchor.getAttribute('data-title') || lines[0] || (children.length === 2 ? children[0] : '');
    const artist = text(artistNode) || anchor.getAttribute('data-artist') || lines[1] || (children.length === 2 ? children[1] : '');
    if (!title || !artist || title.length > 300 || artist.length > 300 || title === artist) continue;
    seen.add(match[1]); url.search = ''; url.hash = '';
    results.push({ id: match[1], source: 'songsterr', title, artist, url: url.href });
  }
  const historyRoot = document.querySelector('#revisions-list, [data-testid="revisions-list"], [class*="revisions-list"]');
  const approved = [], revisionSeen = new Set();
  const approvalLabels = all(historyRoot || document, '*').filter((node) => visible(node) && /^approved$/i.test(text(node)) && !Array.from(node.children || []).some((child) => /^approved$/i.test(text(child))));
  for (const badge of approvalLabels) {
    for (let row = badge.parentElement, depth = 0; row && depth < 7; row = row.parentElement, depth++) {
      const rowText = text(row);
      if (rowText.length > 5000) break;
      const ids = new Set();
      const attr = row.getAttribute?.('data-revision-id'); if (/^[1-9]\d{0,11}$/.test(attr || '')) ids.add(attr);
      for (const anchor of all(row, 'a[href]')) {
        let url; try { url = new URL(anchor.getAttribute('href'), current); } catch { continue; }
        if (url.origin !== current.origin) continue;
        const revisions = [...url.pathname.matchAll(/r([1-9]\d{0,11})/g)];
        const id = revisions.at(-1)?.[1]; if (id) ids.add(id);
      }
      const badges = all(row, '*').filter((node) => /^approved$/i.test(text(node)) && !(node.children?.length));
      if (ids.size > 1 || badges.length > 1) break;
      // The current revision has no View link in the observed history list;
      // it is identified by the comparison URL and its own dated Approved row.
      const date = rowText.match(/\b(\d{1,2}\/\d{1,2}\/\d{4})\b/)?.[1];
      if (!ids.size && historyRoot && currentRevision && date && !all(row, 'a, button').some((node) => /^view$/i.test(label(node)))) ids.add(currentRevision);
      if (ids.size === 1) {
        const id = [...ids][0];
        if (!revisionSeen.has(id)) { approved.push({ revisionId: id, approval: 'approved', date: date || null }); revisionSeen.add(id); }
        break;
      }
    }
  }
  const audio = [];
  for (const node of all(document, 'iframe[src], a[href]').filter(visible)) {
    const value = node.getAttribute('src') || node.getAttribute('href');
    let url; try { url = new URL(value, current); } catch { continue; }
    if (url.protocol === 'https:' && ['www.youtube.com', 'youtube.com', 'youtu.be', 'www.youtube-nocookie.com'].includes(url.hostname)) audio.push(url.href);
  }
  const copyForm = controls.some((node) => /^create$/i.test(label(node))) && all(document, 'input').filter(visible).some((node) => !['password', 'hidden', 'checkbox'].includes(node.getAttribute('type')));
  const noResults = /no (?:songs|tabs|results)(?: found| match|$)/i.test(body);
  const searchReady = results.length > 0 || noResults;
  return { status: loginRequired ? 'needs_login' : 'ready', url: current.href, songId: songMatch?.[1] || null,
    revisionId: currentRevision, signedOut, searchReady, results, noResults, historyVisible: Boolean(historyRoot), approvedRevisions: approved,
    copyForm, unpublished: /\bnot published\b/i.test(body), editor: Boolean(document.querySelector('#control-export-gp')),
    canExport: visible(document.querySelector('#control-export-gp')),
    audio, hasMore: controls.some((node) => /^(?:next|load more|show more)$/i.test(label(node))) };
}

function actOnSongsterrPage(request = {}) {
  const visible = (node) => node && !node.hidden && node.getAttribute?.('aria-hidden') !== 'true' && node.style?.display !== 'none' && (!node.getClientRects || node.getClientRects().length > 0);
  const text = (node) => String(node?.getAttribute?.('aria-label') || node?.getAttribute?.('title') || node?.textContent || '').replace(/\s+/g, ' ').trim();
  const controls = Array.from(document.querySelectorAll('a, button, [role="button"]')).filter(visible);
  const click = (node) => {
    if (!visible(node) || node.disabled || node.getAttribute?.('aria-disabled') === 'true') return { ok: false, reason: 'control_unavailable' };
    node.click(); return { ok: true };
  };
  let current;
  try { current = new URL(String(globalThis.location?.href || document.URL)); } catch { return { ok: false, reason: 'invalid_page' }; }
  if (current.origin !== 'https://www.songsterr.com') return { ok: false, reason: 'wrong_origin' };
  if (request.action === 'history') {
    const byId = document.querySelector('#control-revisions, #control-revision-history');
    const candidates = controls.filter((node) => /^(?:revisions|revision history|\d{1,2}\/\d{1,2}\/\d{4})$/i.test(text(node)));
    return click(byId || (candidates.length === 1 ? candidates[0] : null));
  }
  if (request.action === 'editor') return click(document.querySelector('#control-editor') || controls.find((node) => /^editor$/i.test(text(node))));
  if (request.action === 'copy') return click(controls.find((node) => /^make a copy$/i.test(text(node))));
  if (request.action === 'create') {
    const candidates = controls.filter((node) => /^create$/i.test(text(node)));
    return click(candidates.length === 1 ? candidates[0] : null);
  }
  if (request.action === 'exportMenu') {
    const downloads = controls.filter((node) => /^download$/i.test(text(node)));
    return click(document.querySelector('#control-export') || (downloads.length === 1 ? downloads[0] : null));
  }
  if (request.action === 'export') return click(document.querySelector('#control-export-gp'));
  if (request.action === 'dismissTutorial') return click(controls.find((node) => /^(?:skip tutorial|skip tour|skip)$/i.test(text(node))));
  // Deliberately no publish/delete action, arbitrary selector or URL input.
  return { ok: false, reason: 'unsupported_action' };
}

module.exports = { readSongsterrPage, actOnSongsterrPage };
