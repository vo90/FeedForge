The offline Electron integration suite exercises real Chromium documents,
BrowserWindows, IPC and DownloadItems. It uses a fresh dedicated app/session
profile outside the checkout and intercepts all fixture-session HTTP/HTTPS
responses. It never accesses live websites, credentials or a song library.

For the actual production renderer's navigation, queue and recovery controls, use
the separate [UI integration suite](ui-integration.md).

Run with an installed Electron executable and a **fresh absolute output path**:

```powershell
node test-song-browser/electron-runner.cjs --electron 'C:\path\to\electron.exe' --runtime 'C:\path\to\verification\fresh-run'
```

The runner checks the Electron exit status and `result.json`; stdout/stderr are
retained in the output directory. A nonzero exit or missing report fails the run.
The fixture never adds localhost, insecure content, a test switch or other
exceptions to the production browser adapter. Production origins are used only
as synthetic document identities inside the intercepted test session.

Coverage: real DOM search/pagination, signed-button redirects, nested popup
download ownership/cleanup, expired-button refresh without duplicate clicks,
in-progress cancellation, interrupted response, and actual trusted/foreign
renderer IPC. Synthetic PSARC-shaped bytes are downloaded; conversion and live
host compatibility are separate checks. Chromium stderr for the intentionally
rejected IPC sender is expected.
