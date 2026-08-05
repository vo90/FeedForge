const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

const DEFAULT_SCHEME = "feedforge-local";
const IMAGE_MIME_TYPES = new Map([
  [".gif", "image/gif"],
  [".jpeg", "image/jpeg"],
  [".jpg", "image/jpeg"],
  [".png", "image/png"],
  [".webp", "image/webp"]
]);

class LocalAssetRegistry {
  constructor({ scheme = DEFAULT_SCHEME, allowedRoots = [] } = {}) {
    this.scheme = scheme;
    this.allowedRoots = [];
    this.assetsByToken = new Map();
    this.tokensByPath = new Map();
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

  resolve(requestUrl) {
    let parsed;
    try {
      parsed = new URL(requestUrl);
    } catch {
      return null;
    }
    if (parsed.protocol !== `${this.scheme}:` || parsed.hostname !== "asset") return null;
    const token = parsed.pathname.replace(/^\/+/, "");
    if (!/^[a-f0-9]{32}$/i.test(token) || parsed.search || parsed.hash) return null;
    const registered = this.assetsByToken.get(token);
    if (!registered) return null;

    const resolved = realImageFile(registered);
    if (!resolved || !samePath(resolved, registered)) return null;
    if (!this.allowedRoots.some((root) => isInside(root, resolved))) return null;
    return resolved;
  }

  clear() {
    this.assetsByToken.clear();
    this.tokensByPath.clear();
  }
}

function contentTypeForImage(filePath) {
  return IMAGE_MIME_TYPES.get(path.extname(String(filePath || "")).toLowerCase()) || null;
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
    return fs.statSync(resolved).isFile() ? resolved : null;
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
  contentTypeForImage
};
