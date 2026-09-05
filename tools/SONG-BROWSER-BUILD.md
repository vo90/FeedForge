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
