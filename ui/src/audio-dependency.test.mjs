import assert from "node:assert/strict";
import test from "node:test";

import {
  checkAudioDecoder,
  isPsarcPath,
  normalizeAudioDecoderStatus,
  queueRequiresAudioDecoder
} from "./audio-dependency.mjs";

test("PSARC queues require the WEM decoder while FeedPak-only queues do not", () => {
  assert.equal(isPsarcPath("C:/songs/example.PSARC"), true);
  assert.equal(isPsarcPath("C:/songs/example.feedpak"), false);
  assert.equal(queueRequiresAudioDecoder([{ path: "one.feedpak" }, { path: "two.psarc" }]), true);
  assert.equal(queueRequiresAudioDecoder([{ path: "one.feedpak" }]), false);
});

test("decoder checks fail closed when the preload API is unavailable", async () => {
  const status = await checkAudioDecoder({});
  assert.equal(status.ready, false);
  assert.match(status.message, /PSARC conversion was not started/);
});

test("decoder checks request a refresh and normalize a ready result", async () => {
  let receivedOptions = null;
  const status = await checkAudioDecoder({
    getAudioDecoderStatus: async (options) => {
      receivedOptions = options;
      return { ready: true, available: true, version: "vgmstream CLI decoder r2117" };
    }
  });
  assert.deepEqual(receivedOptions, { refresh: true });
  assert.deepEqual(status, {
    ready: true,
    available: true,
    version: "vgmstream CLI decoder r2117",
    source: "",
    message: "WEM audio decoder is ready.",
    error: ""
  });
});

test("malformed status values cannot accidentally pass preflight", () => {
  assert.equal(normalizeAudioDecoderStatus({ ready: "yes" }).ready, false);
});
