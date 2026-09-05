The UI integration suite builds the real production Vite renderer, loads it with
the production Electron preload, and exercises the Song Browser through its
rendered controls. It runs hidden and uses a fresh profile outside the checkout.
It does not connect to an existing app or require a CustomsForge login.

Choose an installed Electron binary matching the repository dependency and a new
output directory whose parent already exists. Check the plan before running:

```powershell
npm run test:song-browser:ui -- --electron 'C:\path\to\electron.exe' --runtime 'C:\path\to\verification\fresh-ui-run' --mode plan
npm run test:song-browser:ui -- --electron 'C:\path\to\electron.exe' --runtime 'C:\path\to\verification\fresh-ui-run' --mode run
```

The default expected branch is `feat/customsforge-song-browser`. When testing a
deliberately merged branch, supply `--expected-branch BRANCH`; the original
FeedForge baseline must still be an ancestor. The runner verifies branch,
revision, source hashes, installed dependency identities and binary identities
before launching. It records those checks and the built renderer hashes in
`launch-receipt.json`. Existing output is never overwritten or repaired.

The suite checks search results and drafts across Convert/Find songs navigation,
including navigation while a search is pending; cancellation that releases the
queue; retry from cached PSARC after a conversion failure; and missing output
files reflected in both history and search results. It also verifies that an
unchecked session and a failed page load do not imply sign-out, that the rendered
account actions invoke the correct production IPC, and that login, challenge and
successful retry states retain their respective controls.

The browser adapter, IPC boundary, queue, cache, file publication and UI are real.
All HTTP/HTTPS responses are intercepted synthetic CustomsForge and host pages;
unknown requests fail the run. A deterministic converter stub supplies inspection,
conversion and validation responses. The generated files are test bytes, not
playable FeedPaks. Unrelated app startup IPC receives inactive fixture responses.
No server, game, plugin, native dialog or existing user profile is used.

`result.json` records scenario results and fixture counters. Build and Electron
stdout/stderr logs remain alongside it, including on failure. A missing report,
failed assertion, unexpected request, renderer error, timeout or nonzero Electron
exit fails the run. Real host compatibility, real converter output and gameplay
remain separate integration checks.
