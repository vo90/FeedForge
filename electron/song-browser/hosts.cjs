'use strict';

// Host policy belongs to this optional feature. No host URL or access token is
// persisted in the queue; the browser adapter owns each live transfer.
const MAX_BYTES = 512 * 1024 * 1024;
const HOSTS = Object.freeze({
  'google-drive': Object.freeze({ id: 'google-drive', label: 'Google Drive', supported: true, status: 'verified' }),
  dropbox: Object.freeze({ id: 'dropbox', label: 'Dropbox', supported: true, status: 'verified' }),
  mediafire: Object.freeze({ id: 'mediafire', label: 'MediaFire', supported: true, status: 'verified' }),
  onedrive: Object.freeze({ id: 'onedrive', label: 'OneDrive', supported: true, status: 'experimental' }),
  pcloud: Object.freeze({ id: 'pcloud', label: 'pCloud', supported: true, status: 'experimental' }),
  mega: Object.freeze({ id: 'mega', label: 'MEGA', supported: true, status: 'experimental' }),
});
const UNKNOWN = Object.freeze({ id: 'unknown', label: 'Other host', supported: false, status: 'unsupported' });
const SUBDOMAIN = /^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$/;
function inDomain(host, domain) {
  return host === domain || (host.endsWith('.' + domain) && SUBDOMAIN.test(host));
}
function secureUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && !url.username && !url.password && (!url.port || url.port === '443') ? url : null;
  } catch { return null; }
}
function hostFromUrl(value) {
  const url = secureUrl(value);
  if (!url) return 'unknown';
  const host = url.hostname;
  if (['drive.google.com', 'docs.google.com', 'drive.usercontent.google.com'].includes(host) || inDomain(host, 'googleusercontent.com')) return 'google-drive';
  if (['dropbox.com', 'www.dropbox.com'].includes(host) || inDomain(host, 'dropboxusercontent.com')) return 'dropbox';
  if (['mediafire.com', 'www.mediafire.com'].includes(host) || /^download\d+\.mediafire\.com$/.test(host)) return 'mediafire';
  if (['1drv.ms', 'onedrive.live.com', 'api.onedrive.com'].includes(host) || inDomain(host, 'files.1drv.com') || inDomain(host, 'sharepoint.com')) return 'onedrive';
  if (inDomain(host, 'pcloud.com') || inDomain(host, 'pcloud.link')) return 'pcloud';
  if (['mega.nz', 'www.mega.nz', 'mega.co.nz', 'www.mega.co.nz'].includes(host)) return 'mega';
  return 'unknown';
}
function getHostCapabilities(id) { return HOSTS[id] || UNKNOWN; }
function allowedNavigation(value, context = {}) {
  const url = secureUrl(value);
  if (!url) return false;
  if (['customsforge.com', 'ignition4.customsforge.com', 'accounts.google.com', 'login.live.com', 'login.microsoftonline.com'].includes(url.hostname)) return true;
  const host = hostFromUrl(value);
  return getHostCapabilities(host).supported;
}
function allowedDownload(value, filename, bytes, context = {}) {
  if (typeof filename !== 'string' || /[\\/\x00-\x1f\x7f]/.test(filename) || !/\.psarc$/i.test(filename)
      || !Number.isFinite(bytes) || bytes < 0 || bytes > MAX_BYTES) return false;
  if (/_m\.psarc$/i.test(filename) && context.allowMacFallback !== true) return false;
  if (typeof value === 'string' && value.startsWith('blob:')) {
    // MEGA support must explicitly attest an owned, active main-frame
    // document. Merely allowing blob: would accept downloads from any origin.
    if (context.enableMegaBlob !== true || context.ownedWindow !== true || context.host !== 'mega') return false;
    const origin = secureUrl(context.documentUrl || context.origin);
    if (!origin || !['mega.nz', 'www.mega.nz'].includes(origin.hostname)) return false;
    try {
      const blob = new URL(value);
      return blob.origin === origin.origin && /^blob:https:\/\/(?:www\.)?mega\.nz\/[a-zA-Z0-9-]+$/.test(value);
    } catch { return false; }
  }
  const url = secureUrl(value);
  if (!url) return false;
  const host = hostFromUrl(value);
  if (context.host && host !== context.host) return false;
  if (!getHostCapabilities(host).supported) return false;
  const name = url.hostname;
  if (host === 'google-drive') return name === 'drive.usercontent.google.com' || inDomain(name, 'googleusercontent.com');
  if (host === 'dropbox') return true;
  if (host === 'mediafire') return /^download\d+\.mediafire\.com$/.test(name);
  if (host === 'onedrive') return name === 'onedrive.live.com' || name === 'api.onedrive.com' || inDomain(name, 'files.1drv.com') || inDomain(name, 'sharepoint.com');
  if (host === 'pcloud') return /^(?:c|e)\d+\.pcloud\.com$/.test(name) || ['my.pcloud.com', 'e.pcloud.com'].includes(name);
  return false;
}
module.exports = { HOSTS, MAX_BYTES, hostFromUrl, getHostCapabilities, allowedNavigation, allowedDownload };
