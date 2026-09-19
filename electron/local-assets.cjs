const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const { Readable } = require("node:stream");

const DEFAULT_SCHEME = "feedforge-local";
const IMAGE_MIME_TYPES = new Map([
  [".gif", "image/gif"],
  [".jpeg", "image/jpeg"],
  [".jpg", "image/jpeg"],
  [".png", "image/png"],
  [".webp", "image/webp"]
]);
const AUDIO_MIME_TYPES = new Map([[".ogg", "audio/ogg"], [".mp3", "audio/mpeg"], [".wav", "audio/wav"], [".flac", "audio/flac"], [".m4a", "audio/mp4"]]);

class LocalAssetRegistry {
  constructor({ scheme = DEFAULT_SCHEME, allowedRoots = [] } = {}) {
    this.scheme = scheme;
    this.allowedRoots = [];
    this.assetsByToken = new Map();
    this.tokensByPath = new Map();
    this.mediaPaths = new Set();
    for (const root of allowedRoots) this.addAllowedRoot(root);
  }

  addAllowedRoot(root) {
    const resolved = realDirectory(root);
    if (!resolved || this.allowedRoots.some((candidate) => samePath(candidate, resolved))) return false;
    this.allowedRoots.push(resolved);
    return true;
  }

  register(filePath) {
    const resolved = realImageFile(filePath);
    if (!resolved || !this.allowedRoots.some((root) => isInside(root, resolved))) return null;
    const existing = this.tokensByPath.get(pathKey(resolved));
    if (existing) return `${this.scheme}://asset/${existing}`;

    const token = crypto.randomUUID().replaceAll("-", "");
    this.assetsByToken.set(token, resolved);
    this.tokensByPath.set(pathKey(resolved), token);
    return `${this.scheme}://asset/${token}`;
  }

  // Audio is admitted only from an individual owned preview directory. Broad
  // artwork roots never grant access to audio files or arbitrary local paths.
  registerMedia(directory, filePath) {
    const root = realDirectory(directory);
    const resolved = realMediaFile(filePath);
    if (!root || !resolved || !isInside(root, resolved) || fs.lstatSync(filePath).isSymbolicLink()) return null;
    const key = pathKey(resolved);
    const existing = this.tokensByPath.get(key);
    if (existing) return `${this.scheme}://asset/${existing}`;
    const token = crypto.randomUUID().replaceAll("-", "");
    this.assetsByToken.set(token, resolved);
    this.tokensByPath.set(key, token);
    this.mediaPaths.add(key);
    return `${this.scheme}://asset/${token}`;
  }

  revokeDirectory(directory) {
    for (const [token, filename] of this.assetsByToken) {
      if (!isInside(path.resolve(directory), filename)) continue;
      this.assetsByToken.delete(token);
      this.tokensByPath.delete(pathKey(filename));
      this.mediaPaths.delete(pathKey(filename));
    }
  }

  resolve(requestUrl) {
    let parsed;
    try {
      parsed = new URL(requestUrl);
    } catch {
      return null;
    }
    if (parsed.protocol !== `${this.scheme}:` || parsed.hostname !== "asset"
      || parsed.username || parsed.password || parsed.port) return null;
    const token = parsed.pathname.replace(/^\/+/, "");
    if (!/^[a-f0-9]{32}$/i.test(token) || parsed.search || parsed.hash) return null;
    const registered = this.assetsByToken.get(token);
    if (!registered) return null;

    const media = this.mediaPaths.has(pathKey(registered));
    const resolved = media ? realMediaFile(registered) : realImageFile(registered);
    if (!resolved || !samePath(resolved, registered)) return null;
    if (!media && !this.allowedRoots.some((root) => isInside(root, resolved))) return null;
    return resolved;
  }

  clear() {
    this.assetsByToken.clear();
    this.tokensByPath.clear();
    this.mediaPaths.clear();
  }
}

function contentTypeForImage(filePath) {
  return IMAGE_MIME_TYPES.get(path.extname(String(filePath || "")).toLowerCase()) || null;
}

function contentTypeForAsset(filePath) {
  return contentTypeForImage(filePath) || AUDIO_MIME_TYPES.get(path.extname(String(filePath || "")).toLowerCase()) || null;
}

function realMediaFile(value) {
  if (!value || !AUDIO_MIME_TYPES.has(path.extname(String(value)).toLowerCase())) return null;
  try {
    const resolved = fs.realpathSync.native(path.resolve(String(value)));
    return AUDIO_MIME_TYPES.has(path.extname(resolved).toLowerCase()) && fs.statSync(resolved).isFile() ? resolved : null;
  } catch { return null; }
}

async function localAssetResponse(registry, request) {
  const filename = registry?.resolve(request.url);
  const contentType = contentTypeForAsset(filename);
  if (!filename || !contentType) return new Response("Not found", { status: 404 });
  const method = request.method || "GET";
  if (!["GET", "HEAD"].includes(method)) return new Response(null, { status: 405 });
  try {
    const size = (await fs.promises.stat(filename)).size;
    const headers = { "Content-Type": contentType, "Cache-Control": "private, no-store",
      "X-Content-Type-Options": "nosniff", "Accept-Ranges": "bytes" };
    const range = request.headers?.get?.("range");
    let start = 0, end = size - 1, status = 200;
    if (range) {
      const match = /^bytes=(\d*)-(\d*)$/.exec(range);
      if (!match || (!match[1] && !match[2])) return new Response(null, { status: 416, headers: { "Content-Range": `bytes */${size}` } });
      if (!match[1]) start = Math.max(0, size - Number(match[2]));
      else { start = Number(match[1]); if (match[2]) end = Math.min(end, Number(match[2])); }
      if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start < 0 || start > end || start >= size) {
        return new Response(null, { status: 416, headers: { "Content-Range": `bytes */${size}` } });
      }
      status = 206;
      headers["Content-Range"] = `bytes ${start}-${end}/${size}`;
    }
    headers["Content-Length"] = String(Math.max(0, end - start + 1));
    const body = method === "HEAD" || size === 0 ? null : Readable.toWeb(fs.createReadStream(filename, { start, end }));
    return new Response(body, { status, headers });
  } catch { return new Response("Not found", { status: 404 }); }
}

function realDirectory(value) {
  if (!value) return null;
  try {
    const resolved = fs.realpathSync.native(path.resolve(String(value)));
    return fs.statSync(resolved).isDirectory() ? resolved : null;
  } catch {
    return null;
  }
}

function realImageFile(value) {
  if (!value || !contentTypeForImage(value)) return null;
  try {
    const resolved = fs.realpathSync.native(path.resolve(String(value)));
    return contentTypeForImage(resolved) && fs.statSync(resolved).isFile() ? resolved : null;
  } catch {
    return null;
  }
}

function isInside(root, candidate) {
  const relative = path.relative(root, candidate);
  return Boolean(relative) && !relative.startsWith(`..${path.sep}`) && relative !== ".." && !path.isAbsolute(relative);
}

function samePath(left, right) {
  return pathKey(left) === pathKey(right);
}

function pathKey(value) {
  const resolved = path.resolve(value);
  return process.platform === "win32" ? resolved.toLowerCase() : resolved;
}

module.exports = {
  DEFAULT_SCHEME,
  LocalAssetRegistry,
  contentTypeForImage,
  contentTypeForAsset,
  localAssetResponse
};
