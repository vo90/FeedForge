# FeedForge Song Browser

A desktop feature for individual CustomsForge searches and local PSARC → FeedPak jobs.
Development branch: `feat/customsforge-song-browser`, based on FeedForge 0.1.40 (`804aa8c`).

## Code boundaries

- `dom.cjs`: rendered CustomsForge search, pagination and Windows download controls. Self-contained functions run in the site document; signed URLs never leave that document.
- `browser.cjs`: app-owned persistent browser session, restricted remote windows, host navigation, progress and download lifetime.
- `host-actions.cjs`: ordinary public-page actions for Dropbox, Google Drive and MediaFire. No website API or cookie extraction.
- `jobs.cjs`: sequential queue, inspection, conversion, validation, duplicate detection, publication and cancellation. Uses an injected `runConverter` function.
- `index.cjs`: narrow IPC registration, settings and trusted-main-frame checks.
- `ui/src/song-browser/`: self-contained React component and scoped stylesheet.

The Python converter is unchanged. Existing FeedForge files have small integration hooks: registration and development runtime overrides in `electron/main.cjs`, a preload bridge, a navigation/component entry in `ui/src/main.jsx`, and a test command. There are no new dependency requirements. This is an optional module inside FeedForge rather than a separate converter fork; it can be reviewed or ported as a feature commit.

## Using the development build

1. Launch the isolated development build with `tools/Start-SongBrowser.ps1`. The launcher validates branch/baseline, the Python runner, UI build and runtime ownership. Supply explicit Python, Electron, audio-tool and runtime paths; use `-CheckOnly` first. A new runtime directory must be outside the checkout. The existing normal FeedForge profile is not used.
2. Select **Find songs → Sign in** and complete the ordinary CustomsForge login. This session belongs to this app, so the Codex browser login is separate.
3. Choose an output folder. Select your configured FeedBack song-library folder (or a subfolder of it) to place new songs there. Initial output defaults to this development profile's own FeedPaks folder.
4. Search for an artist/title, compare creator/version/tuning/instruments, then select **Download & convert**. CustomsForge's normal download action also adds the chart to the account's collection.
5. If a host needs sign-in, a security check or another manual step, use **Open browser**. The queue resumes when a valid PSARC download completes. Cancel is available while waiting.
6. In an already-running FeedBack, choose **Songs → Refresh** to display new files immediately. Its installed scanner also scans periodically (300 seconds); this feature does not currently call the running game's rescan API or guarantee that an already-open song grid repaints automatically.

## Scope and behavior

- Supported automatic paths: public individual PSARC links on Dropbox, Google Drive and MediaFire. Only the Windows chart button is chosen. MEGA, OneDrive, archives and folder selection are not automatically supported.
- Maximum input: 512 MiB. HTML, empty files, wrong song identities, multi-song packages and invalid packages are rejected. Download/host titles can differ from archive metadata; ambiguous identities require manual review.
- No background catalog crawl. Search runs on explicit submission; pagination uses visible website controls. Download uses a fresh record page and current signed button. A collection click is never blindly retried after an ambiguous response.
- The converter runs without stem separation. A file is published only after independent FeedPak validation. Existing filenames are preserved; collisions gain a suffix. A recorded unchanged output can be reused after source-hash comparison.
- Atomic publication uses a temporary file in the output folder and an exclusive hardlink. Choose a filesystem supporting hardlinks (such as NTFS); unsupported output filesystems fail without publishing a partial file.
- History is local and bounded. Interrupted work is marked failed on restart, not automatically downloaded again. Active transfers and converter children finish cancellation before their owned cache is cleaned. Browser credentials are held by Electron's separate browser profile and are never copied into the job ledger or application bridge.
- This adapter follows the current website UI, not a published integration API. Site changes can require adapter updates. Live sign-in and the entire new desktop-browser flow still need a user-assisted test in this development profile.

## Verification

`npm run test:song-browser` runs Node tests for DOM parsing, origin/host checks, download lifetime, queue errors, cancellation, collision handling and validation. `npm run build` verifies production UI compilation.

`test-song-browser/ui-fixture.html` is a development-only visual/interaction fixture with conspicuously labelled simulated results. It is not included in the production entry. Use a Vite development server to inspect it.

`test-song-browser/real-conversion.cjs --input <existing.psarc> --python <python> --audio-tools <directory> --root <new-smoke-directory>` exercises the actual queue and converter with a locally supplied PSARC. It performs no network download and never writes into a real library. A BTS sample successfully completed conversion, independent validation and duplicate-output reuse during implementation. Browser download behavior was unit-tested; the earlier live research separately verified real files from all three hosts.
