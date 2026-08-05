const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const { LocalAssetRegistry, contentTypeForImage } = require("./local-assets.cjs");

function fixture() {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), "feedforge-assets-test-"));
  const allowed = path.join(base, "allowed");
  const outside = path.join(base, "outside");
  fs.mkdirSync(allowed);
  fs.mkdirSync(outside);
  fs.writeFileSync(path.join(allowed, "cover.png"), Buffer.from("png"));
  fs.writeFileSync(path.join(outside, "secret.png"), Buffer.from("secret"));
  fs.writeFileSync(path.join(allowed, "notes.txt"), Buffer.from("text"));
  return { base, allowed, outside };
}

test("registers and resolves an allow-listed image without exposing its path", (t) => {
  const files = fixture();
  t.after(() => fs.rmSync(files.base, { recursive: true, force: true }));
  const registry = new LocalAssetRegistry({ allowedRoots: [files.allowed] });
  const coverPath = path.join(files.allowed, "cover.png");

  const url = registry.register(coverPath);

  assert.match(url, /^feedforge-local:\/\/asset\/[a-f0-9]{32}$/);
  assert.equal(url.includes("cover.png"), false);
  assert.equal(registry.resolve(url), fs.realpathSync.native(coverPath));
  assert.equal(registry.register(coverPath), url);
});

test("rejects unregistered, outside-root, and non-image files", (t) => {
  const files = fixture();
  t.after(() => fs.rmSync(files.base, { recursive: true, force: true }));
  const registry = new LocalAssetRegistry({ allowedRoots: [files.allowed] });

  assert.equal(registry.register(path.join(files.outside, "secret.png")), null);
  assert.equal(registry.register(path.join(files.allowed, "notes.txt")), null);
  assert.equal(registry.resolve("feedforge-local://asset/00000000000000000000000000000000"), null);
  assert.equal(registry.resolve("file:///C:/Windows/win.ini"), null);
});

test("returns image content types", () => {
  assert.equal(contentTypeForImage("cover.PNG"), "image/png");
  assert.equal(contentTypeForImage("cover.jpeg"), "image/jpeg");
  assert.equal(contentTypeForImage("notes.txt"), null);
});
