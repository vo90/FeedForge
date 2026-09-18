# Portable Song Browser test build

`Build-SongBrowser.ps1` uses PowerShell 7.2 or newer, the already installed Node/Electron builder dependencies, an explicit Python environment with PyInstaller, and explicit audio-tool binaries. It does not install dependencies or download a different runtime. The resulting ZIP contains a standalone Windows x64 app folder, including its frozen Python converter, so the recipient does not need Python or Node.

Use paths appropriate to your machine. The build folder must be outside the source checkout and either new or owned by this script. Normal FeedForge packaging is unchanged.

```powershell
$build = @{
    PythonPath = 'C:\path\to\python.exe'
    ElectronPath = 'C:\path\to\electron\dist\electron.exe'
    AudioToolsPath = 'C:\path\to\audio-tools'
    BuildRoot = 'C:\builds\song-browser-test-001'
}
.\tools\Build-SongBrowser.ps1 @build -CheckOnly
.\tools\Build-SongBrowser.ps1 @build
```

For a staged build, use `-Stage Converter`, followed later by `-Stage Package` with the same paths. The package stage checks that converter source has not changed since freezing. Failed or completed stage outputs are preserved; use a new build folder for a fresh attempt. No output cleanup/reset option is provided.

The converter specification uses this checkout's source and bundles `vgmstream-cli.exe`, `ffmpeg.exe` and the matching decoder DLLs. The packaging configuration has a distinct name (`FeedForge Song Browser Test`), app ID (`com.feedforge.songbrowser.test`) and the `songBrowserTest: true` package marker consumed by the desktop startup code. This marker isolates its profile, opens Find songs and disables official updater operations.

The script builds the UI into its external staging directory. The output contains `release\win-unpacked\FeedForge Song Browser Test.exe` and a ZIP to extract before running. Keep the entire extracted folder together. Build receipt and dependency details stay outside the source checkout; no account session is included.

Validate the frozen converter with a locally supplied PSARC and a new disposable smoke folder:

```powershell
node tools\Test-SongBrowserConverter.cjs --converter 'C:\builds\song-browser-test-001\converter-dist\psarc2feedpak\psarc2feedpak.exe' --input 'C:\songs\existing.psarc' --root 'C:\builds\song-browser-test-001\standalone-smoke'
```

This smoke removes Python/FeedForge environment variables and restricts `PATH` to Windows system folders. It verifies inspection, conversion, independent FeedPak validation, Ogg/Vorbis audio, and an unchanged source file. It makes no network requests and never imports into a game library. Live CustomsForge sign-in, the supported hosts and FeedBack playback remain separate user-assisted checks.

## Verify a packaged artifact without launching the app

`Test-SongBrowserPackage.cjs` inspects the extracted Windows app, ASAR modules, production assets, executable product identity and bundled converter. It also executes the packaged startup logic in a VM with mocked Electron and an in-memory filesystem to check profile isolation, Find songs as the initial view, registration hooks, title protection, converter selection and disabled official updater actions. Network, external process, dialog and ordinary portable-cleanup requests are denied in that simulation.

```powershell
node tools\Test-SongBrowserPackage.cjs --package-root 'C:\builds\song-browser-test-001\release\win-unpacked' --root 'C:\builds\song-browser-test-001\package-verification' --source-root 'C:\source\FeedForge' --ui-stage 'C:\builds\song-browser-test-001\app-stage\desktop-dist'
```

The report folder must be new and outside the package and source directories. `--source-root` compares every packaged Electron module with the supplied checkout; `--ui-stage` compares packaged UI files with the supplied compiled UI. Omit either argument when that comparison is unavailable; the report marks it as not requested. A different source revision fails the comparison rather than silently passing.

Add `--psarc 'C:\songs\existing.psarc'` to run the existing standalone-converter smoke against the converter inside this package. That optional step executes only the converter and places its output in the new report folder. Without `--psarc`, no packaged executable is run.

This command does not launch Electron, use browser debugging or UI automation, inspect an existing account profile, contact external hosts, or test real sign-in/download/playback. `result.json` distinguishes artifact checks and simulated startup from the optional real converter test. Actual desktop behavior remains a separate manual or supported computer-use check.
