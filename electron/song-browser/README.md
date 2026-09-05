# FeedForge Song Browser

A desktop feature for individual CustomsForge searches and local PSARC → FeedPak jobs.
Development branch: `feat/customsforge-song-browser`, based on FeedForge 0.1.40 (`804aa8c`).

## Code boundaries

- `dom.cjs`: rendered CustomsForge search, pagination and Windows download controls. Self-contained functions run in the site document; signed URLs never leave that document.
- `browser.cjs`: app-owned persistent browser session, restricted remote windows, host navigation, progress and download lifetime.
- `host-actions.cjs`: ordinary public-page actions for Dropbox, Google Drive and MediaFire. No website API or cookie extraction.
- `jobs.cjs`: sequential queue, inspection, conversion, validation, duplicate detection, publication and cancellation. Uses an injected `runConverter` function.
- `index.cjs`: narrow IPC registration, settings and trusted-main-frame checks.
- `diagnostics.cjs`: bounded reports with fixed codes, stages, host categories and timing.
- `feedback.cjs`: optional loopback-only connection to FeedBack's existing library refresh API.
- `ui/src/song-browser/`: self-contained React component and scoped stylesheet.

The Python converter is unchanged. Existing FeedForge files have small integration hooks: registration and development runtime overrides in `electron/main.cjs`, a preload bridge, a navigation/component entry in `ui/src/main.jsx`, and a test command. There are no new dependency requirements. This is an optional module inside FeedForge rather than a separate converter fork; it can be reviewed or ported as a feature commit.

## Using the development build

1. Extract the entire portable test ZIP, then open **FeedForge Song Browser Test.exe** inside `win-unpacked`. Python, Node and a source checkout are not needed. The ZIP's complete folder must stay together. Its profile is separate from both normal FeedForge and the earlier development launcher. For source development, `tools/Start-SongBrowser.ps1` remains available; packaging instructions are in `tools/SONG-BROWSER-BUILD.md`.
2. Select **Find songs → Sign in** and complete the ordinary CustomsForge login. This session belongs to this app, so the Codex browser login is separate.
3. Choose an output folder. Select your configured FeedBack song-library folder (or a subfolder of it) to place new songs there. Initial output defaults to this development profile's own FeedPaks folder.
4. Search for an artist/title, compare creator/version/tuning/instruments, then select **Download & convert**. CustomsForge's normal download action also adds the chart to the account's collection.
5. If a host needs sign-in, a security check or another manual step, use **Open browser**. The queue resumes when a valid PSARC download completes. Cancel is available while waiting.
6. To request a game-library refresh automatically, expand **FeedBack connection**, enter the local address used by your running game, select **Connect**, then **Use FeedBack folder** and enable **Refresh FeedBack after each successful conversion**. Connection settings and your output choice are remembered. The bridge checks FeedBack's identity and configured folder before each refresh, and only supports a game on this computer. A refresh request does not prove that the game has finished indexing or that its open song grid has repainted. **Songs → Refresh** remains available in the game.
7. A failed job can be retried. Readable downloaded PSARC files are retained temporarily after recoverable failures, so **Retry conversion** can avoid another download. **Open in FeedForge** asks you to save a separate review copy, then adds it to the normal converter workspace. **Clear cached file** removes only the feature's own temporary copy. HTML, partial and cancelled downloads are not retained for review.
8. If a test fails, use **Export troubleshooting report** before closing the app. It includes this session's last 200 fixed events, app/adapter versions and timing. Reports contain no cookies, signed links, page text, song metadata or local paths; they are saved locally and never sent automatically. Choose a new report filename to preserve earlier reports.

## Scope and behavior

- Supported automatic paths: public individual PSARC links on Dropbox, Google Drive and MediaFire. Only the Windows chart button is chosen. MEGA, OneDrive, archives and folder selection are not automatically supported.
- Maximum input: 512 MiB. HTML, empty files, wrong song identities, multi-song packages and invalid packages are rejected. Download/host titles can differ from archive metadata; ambiguous identities require manual review.
- No background catalog crawl. Search runs on explicit submission; pagination uses visible website controls. Download uses a fresh record page and current signed button. A collection click is never blindly retried after an ambiguous response.
- The converter runs without stem separation. A file is published only after independent FeedPak validation. Existing filenames are preserved; collisions gain a suffix. A recorded unchanged output can be reused after source-hash comparison. Changing output folders copies an existing validated package into the new folder without reconverting; it never silently substitutes a file in the previous folder.
- Atomic publication uses a temporary file in the output folder and an exclusive hardlink. Actual write/hardlink probes run when choosing a folder and before downloads. Choose a filesystem supporting hardlinks (such as NTFS). Probes are removed; unsupported output filesystems fail before downloading. Native filesystem paths keep containment, cache and duplicate checks consistent with Windows app-data redirection.
- History is local and bounded. Recovery receipts reconcile already-published outputs after restart, including a failure to save the final history entry. Interrupted work is marked failed and waits for an explicit retry. A history-write failure is reported as a warning without changing a successful saved file into a failed conversion.
- The recovery cache is limited to 3 PSARC files, 1 GiB total and 7 days. Active retry sources remain protected while in use. Active transfers and converter children finish cancellation before their owned work folders are cleaned. Browser credentials are held by Electron's separate browser profile and are never copied into the job ledger or application bridge.
- The optional FeedBack bridge reads `/api/version`, `/api/settings` and `/api/scan-status`, then requests `/api/rescan`. It never changes game settings or edits an existing library file. Its folder detection uses the game's reported `dlc_dir`. A manually launched backend must omit `DLC_DIR` or align it with the saved setting: the current API does not report the effective environment override, so a refresh can succeed without discovering a file saved in the reported folder. Normal desktop launches align those paths. A missing/unreachable game does not invalidate a completed FeedPak.
- This adapter follows the current website UI, not a published integration API. Site changes can require adapter updates. Live sign-in and the entire new desktop-browser flow still need a user-assisted test in this development profile.

## Verification

`npm run test:song-browser` runs Node tests for DOM parsing, origin/host checks, download lifetime, queue errors, cancellation, collision handling and validation. `npm run build` verifies production UI compilation.

`npm run test:song-browser:electron -- --electron <absolute-electron.exe> --runtime <new-absolute-directory>` runs real Electron windows, DownloadItems and IPC against intercepted local fixtures. It covers redirects, popups, expired links, cancellation and interrupted streams. All fixture-session HTTP/HTTPS requests are synthesized inside the harness; production origin rules stay unchanged. See `test-song-browser/electron-readme.md`.

`test-song-browser/ui-fixture.html` is a development-only visual/interaction fixture with conspicuously labelled simulated results. It is not included in the production entry. Use a Vite development server to inspect it.

`test-song-browser/feedback-integration.cjs` runs an explicitly selected installed FeedBack backend with its bundled interpreter in a new disposable profile/library. It verifies bridge refresh, actual song discovery, instrument arrangements reaching highway WebSocket readiness, and Ogg audio serving. It also reproduces the `DLC_DIR` limitation. This tests the game APIs; rendered gameplay and audible playback remain separate checks. See `test-song-browser/feedback-integration.md`.

`node test-song-browser/real-conversion.cjs --input <existing.psarc> --python <python> --audio-tools <directory> --root <new-smoke-directory>` exercises the actual queue and converter with a locally supplied PSARC. Frozen-converter mode uses `--converter <psarc2feedpak.exe>` instead of `--python` and `--audio-tools`. It performs no network download and never writes into a real library. It checks conversion, independent validation, duplicate reuse and copying a duplicate into a newly selected folder. `tools/Test-SongBrowserConverter.cjs` separately checks the frozen CLI, including Ogg/Vorbis full-track audio, with Python/FeedForge environment variables removed and PATH restricted to Windows system folders.
