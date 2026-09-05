const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');

// This optional bridge only talks to a user-selected FeedBack on this computer.
function normalizeEndpoint(value) {
  let url;
  try { url = new URL(value); } catch { throw new Error('Enter the local FeedBack address, including http:// and its port.'); }
  if (url.protocol !== 'http:' || !['127.0.0.1', '[::1]', 'localhost'].includes(url.hostname)
    || url.username || url.password || url.search || url.hash || url.pathname !== '/') {
    throw new Error('Use a local FeedBack address such as http://127.0.0.1:8000.');
  }
  // Avoid name resolution, redirects, proxies and credentials for this local bridge.
  if (url.hostname === 'localhost') url.hostname = '127.0.0.1';
  return url.origin;
}

function requestJson(base, route, method = 'GET') {
  if (!['/api/version', '/api/settings', '/api/scan-status', '/api/rescan'].includes(route)
    || (method !== 'GET' && !(method === 'POST' && route === '/api/rescan'))) {
    return Promise.reject(new Error('Unsupported FeedBack operation.'));
  }
  const url = normalizeEndpoint(base) + route;
  return new Promise((resolve, reject) => {
    let timer;
    const fail = () => reject(new Error('FeedBack could not be reached. Check that it is running at the chosen address.'));
    const req = http.request(url, { method, agent: false, headers: { Accept: 'application/json' } }, (res) => {
      if (res.statusCode !== 200 || !/^application\/json\b/i.test(res.headers['content-type'] || '')) {
        res.resume(); reject(new Error('The chosen address did not return a supported FeedBack response.')); return;
      }
      let size = 0;
      const chunks = [];
      res.on('data', (chunk) => {
        size += chunk.length;
        if (size > 65536) { req.destroy(); reject(new Error('FeedBack returned an oversized response.')); }
        else chunks.push(chunk);
      });
      res.on('error', fail);
      res.on('end', () => {
        try { resolve(JSON.parse(Buffer.concat(chunks).toString('utf8'))); }
        catch { reject(new Error('FeedBack returned invalid JSON.')); }
      });
    });
    req.on('error', fail);
    req.on('close', () => clearTimeout(timer));
    timer = setTimeout(() => { req.destroy(); fail(); }, 4000);
    req.end();
  });
}

async function inspectFeedback(endpoint) {
  const url = normalizeEndpoint(endpoint);
  const version = await requestJson(url, '/api/version');
  if (typeof version?.version !== 'string' || !version.version.trim()
    || !/^https:\/\/github\.com\/(?:got-feedback|vo90)\/feedback\/?$/i.test(version.source_url || '')) {
    throw new Error('The chosen address could not be identified as FeedBack.');
  }
  const settings = await requestJson(url, '/api/settings');
  const status = await requestJson(url, '/api/scan-status');
  if (typeof status?.running !== 'boolean') throw new Error('This FeedBack version does not expose a supported library scanner.');
  let libraryDir;
  try {
    if (typeof settings?.dlc_dir !== 'string' || !path.isAbsolute(settings.dlc_dir)) throw new Error();
    libraryDir = fs.realpathSync(settings.dlc_dir);
    if (!fs.statSync(libraryDir).isDirectory()) throw new Error();
  } catch { throw new Error('FeedBack must have an accessible song-library folder configured before connecting.'); }
  return { url, libraryDir, version: version.version.slice(0, 80), running: status.running };
}

async function refreshFeedback(endpoint, outputPath) {
  // Recheck both service identity and library path before every mutation.
  const info = await inspectFeedback(endpoint);
  let target;
  try { target = fs.realpathSync(outputPath); }
  catch { throw new Error('The converted file or output folder is no longer available.'); }
  const relative = path.relative(info.libraryDir, target);
  if (relative === '..' || relative.startsWith('..' + path.sep) || path.isAbsolute(relative)) {
    throw new Error('Choose the connected FeedBack library as your output folder before refreshing it.');
  }
  const result = await requestJson(info.url, '/api/rescan', 'POST');
  if (!['Rescan started', 'Scan already in progress'].includes(result?.message)) {
    throw new Error('FeedBack did not confirm the refresh request. Use Songs → Refresh in the game.');
  }
  return { ...info, message: 'Library refresh requested. Check Songs in FeedBack.' };
}

module.exports = { normalizeEndpoint, inspectFeedback, refreshFeedback };
