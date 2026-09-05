// Runs inside the sandboxed public host document. No network API or credentials.
function hostDownloadAction() {
  const host = location.hostname;
  const text = (document.body?.innerText || '').slice(0, 30000);
  if (/verify you are human|checking your browser|captcha/i.test(document.title + '\n' + text)) {
    return { status: 'challenge', error: 'Complete the host check in the browser.' };
  }
  if (/too many users have viewed or downloaded|download quota|file you have requested does not exist|file has been deleted|you need access|request access/i.test(text)) {
    return { status: 'needs_attention', error: 'The file is restricted, unavailable, or has reached its download limit. Check the browser.' };
  }
  if (host === 'accounts.google.com') return { status: 'login_required', error: 'This file needs a Google sign-in. Continue in the browser.' };
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
  const click = (element) => {
    if (element.hasAttribute(marker)) return { status: 'already_clicked' };
    element.setAttribute(marker, 'true');
    element.click();
    return { status: 'clicked' };
  };
  if (host === 'www.dropbox.com' || host === 'dropbox.com') {
    const url = new URL(location.href);
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
        if (target.protocol === 'https:' && !target.username && !target.password && (!target.port || target.port === '443')
          && /^download\d+\.mediafire\.com$/.test(target.hostname)) return click(link);
      } catch { /* Let the user inspect an unfamiliar page. */ }
    }
    return { status: 'needs_attention', error: 'Use the file download button on MediaFire to continue.' };
  }
  if (host === 'drive.google.com' || host === 'drive.usercontent.google.com') {
    const buttons = [...document.querySelectorAll('button, [role="button"], a, input[type="submit"]')];
    const download = buttons.find((el) => {
      const label = (el.getAttribute('aria-label') || el.getAttribute('data-tooltip') || el.innerText || el.value || '').trim();
      return /^(?:download|download anyway)$/i.test(label) && usable(el);
    });
    if (download) return click(download);
    return { status: 'waiting' };
  }
  return { status: 'waiting' };
}
module.exports = { hostDownloadAction };
