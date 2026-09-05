# Installed FeedBack API integration smoke

`feedback-integration.cjs` runs the actual installed FeedBack backend with its
bundled interpreter and a new disposable profile/library. It never launches
Desktop, modifies installed source, installs dependencies, or uses a normal
song library. Its Python launcher adds only random-token identity and shutdown
routes to the test process; scanner, settings, library, highway and audio routes
are the installed implementation.

Run `--mode plan` first, then repeat with `--mode run`. Required absolute paths
are `--source` (installed `resources/slopsmith`), `--python` (the interpreter
shipped beside it), `--input` (a previously validated FeedPak), and `--root` (a
**new** directory under an existing verification folder). Also provide the
expected installed `--version` and an unused loopback `--port`.

```text
node test-song-browser/feedback-integration.cjs --mode plan --source <installed-source> --python <bundled-python.exe> --input <validated.feedpak> --root <new-runtime> --version 0.3.0-alpha.1 --port 47531 --scenario config
```

The gate checks the feature branch and baseline ancestry, packaged version,
source/plugin hashes, interpreter identity/search path, runtime containment and
port availability. The runtime redirects configuration, home, logs and caches;
disables enrichment and startup tasks; uses one scan worker; and proves zero
loaded plugins. An identity endpoint verifies the exact child PID and effective
library before any API mutation. The test stops only its own backend and checks
source/interpreter/input hashes afterward. Existing or partial test directories
are preserved; use another new root for a subsequent run.

The normal `config` scenario checks the real bridge's inspect/refresh calls,
song discovery, every nonempty instrument arrangement reaching highway `ready`,
and an Ogg audio response. FeedBack may seed its four bundled starter/diagnostic
charts during the scan; the test identifies those separately from the imported
song. Reports and server logs remain under the explicit runtime directory.

This is API integration evidence. It does **not** verify rendered highway
animation, sound-device playback, input latency or human gameplay.

## Known limitation: explicit `DLC_DIR` override

The `env-override` scenario deliberately makes the saved `config.json` library
different from the backend's `DLC_DIR` environment override. On installed
FeedBack 0.3.0-alpha.1, `/api/settings` reports the saved folder, while
`lib/dlc_paths.py::_get_dlc_dir` gives a valid `DLC_DIR` environment variable
priority. The public API used by the bridge does not expose that effective root.

As a result, bridge inspection accepts the saved folder and a rescan request
succeeds, but a FeedPak placed there is absent from the library that was actually
scanned. The reproduction exits nonzero and records `bridgeMismatch` and
`falseRefreshSuccess`; the latter means the refresh request was accepted despite
the imported song not being discovered, not that FeedBack falsely reported a
completed import.

Normal Desktop launches align the configured library and backend environment.
For a manually launched backend, omit `DLC_DIR` or keep it identical to the saved
library setting before using automatic refresh. The harness preserves both
folders as disposable evidence and makes no changes to the core API or bridge.
