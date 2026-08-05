const assert = require("node:assert/strict");
const test = require("node:test");

const { redactConverterArgs } = require("./converter-args.cjs");

test("redacts separated and equals-style API key arguments", () => {
  assert.deepEqual(
    redactConverterArgs([
      "--demucs-url",
      "http://127.0.0.1:7865",
      "--demucs-api-key",
      "top-secret",
      "--demucs-api-key=another-secret",
      "song.psarc"
    ]),
    [
      "--demucs-url",
      "http://127.0.0.1:7865",
      "--demucs-api-key",
      "[redacted]",
      "--demucs-api-key=[redacted]",
      "song.psarc"
    ]
  );
});

test("does not mutate the command arguments used to launch the converter", () => {
  const args = ["--demucs-api-key", "secret", "song.psarc"];
  const redacted = redactConverterArgs(args);

  assert.deepEqual(args, ["--demucs-api-key", "secret", "song.psarc"]);
  assert.notEqual(redacted, args);
});
