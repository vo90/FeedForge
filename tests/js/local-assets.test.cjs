"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const { LocalAssetRegistry, contentTypeForImage } = require("../../electron/local-assets.cjs");

function fixture(t) {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), "feedforge-local-assets-"));
  t.after(() => fs.rmSync(base, { recursive: true, force: true }));
  const allowed = path.join(base, "allowed");
  const outside = path.join(base, "allowed-sibling");
  for (const directory of [allowed, outside]) fs.mkdirSync(directory);
  for (const directory of [allowed, outside]) fs.writeFileSync(path.join(directory, "cover.png"), "image");
  fs.writeFileSync(path.join(allowed, "notes.txt"), "private text");
  return { base, allowed, outside, registry: new LocalAssetRegistry({ allowedRoots: [allowed] }) };
}

test("only registered images receive stable opaque URLs; native paths remain usable", (t) => {
  const { allowed, registry } = fixture(t);
  const imagePath = path.join(allowed, "cover.png");
  const url = registry.register(imagePath);
  assert.match(url, /^feedforge-local:\/\/asset\/[a-f0-9]{32}$/);
  assert.equal(url.includes("cover.png"), false);
  assert.equal(registry.register(imagePath), url);
  assert.equal(registry.resolve(url), fs.realpathSync.native(imagePath));
  assert.equal(fs.readFileSync(imagePath, "utf8"), "image");
});

test("outside roots, sibling-prefix paths, missing files, and non-image files are rejected", (t) => {
  const { allowed, outside, registry } = fixture(t);
  assert.equal(registry.register(path.join(outside, "cover.png")), null);
  assert.equal(registry.register(path.join(allowed, "notes.txt")), null);
  assert.equal(registry.register(path.join(allowed, "missing.png")), null);
  assert.equal(registry.addAllowedRoot(allowed), false);
  assert.equal(registry.addAllowedRoot(path.join(allowed, "missing")), false);
});

test("forged URLs cannot address a path or decorate an existing token", (t) => {
  const { allowed, registry } = fixture(t);
  const url = registry.register(path.join(allowed, "cover.png"));
  for (const invalid of [
    "file:///C:/Windows/win.ini", "not a URL",
    "feedforge-local://asset/00000000000000000000000000000000",
    `${url}?path=cover.png`, `${url}#fragment`, `${url}/cover.png`,
    url.replace("//asset/", "//user@asset/"),
    url.replace("//asset/", "//asset:99/"),
    url.replace("//asset/", "//other/")
  ]) assert.equal(registry.resolve(invalid), null, invalid);
});

test("realpath guards reject an image reached through an escaping directory link", (t) => {
  const { allowed, outside, registry } = fixture(t);
  fs.symlinkSync(outside, path.join(allowed, "linked"), process.platform === "win32" ? "junction" : "dir");
  assert.equal(registry.register(path.join(allowed, "linked", "cover.png")), null);
});

test("a registered directory replaced with an escaping link invalidates its token", (t) => {
  const { allowed, outside, registry } = fixture(t);
  const folder = path.join(allowed, "nested");
  fs.mkdirSync(folder);
  fs.writeFileSync(path.join(folder, "cover.png"), "original");
  const url = registry.register(path.join(folder, "cover.png"));
  fs.renameSync(folder, path.join(allowed, "saved"));
  fs.symlinkSync(outside, folder, process.platform === "win32" ? "junction" : "dir");
  assert.equal(registry.resolve(url), null);
});

test("deleted assets and cleared sessions stop resolving", (t) => {
  const { allowed, registry } = fixture(t);
  const imagePath = path.join(allowed, "cover.png");
  const url = registry.register(imagePath);
  fs.unlinkSync(imagePath);
  assert.equal(registry.resolve(url), null);
  fs.writeFileSync(imagePath, "replacement");
  registry.clear();
  assert.equal(registry.resolve(url), null);
  assert.notEqual(registry.register(imagePath), url);
});

test("supported image MIME types exclude active SVG and text", () => {
  assert.equal(contentTypeForImage("cover.PNG"), "image/png");
  assert.equal(contentTypeForImage("cover.jpeg"), "image/jpeg");
  assert.equal(contentTypeForImage("cover.webp"), "image/webp");
  assert.equal(contentTypeForImage("cover.svg"), null);
  assert.equal(contentTypeForImage("notes.txt"), null);
});
