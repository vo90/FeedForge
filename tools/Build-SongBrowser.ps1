#requires -Version 7.2
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$PythonPath,
    [Parameter(Mandatory = $true)][string]$ElectronPath,
    [Parameter(Mandatory = $true)][string]$AudioToolsPath,
    [Parameter(Mandatory = $true)][string]$BuildRoot,
    [ValidateSet('All', 'Converter', 'Package')][string]$Stage = 'All',
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
$sourceRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$buildPath = [IO.Path]::GetFullPath($BuildRoot).TrimEnd('\', '/')
$sourcePrefix = $sourceRoot.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
if ($buildPath.Equals($sourceRoot, [StringComparison]::OrdinalIgnoreCase) -or
    $buildPath.StartsWith($sourcePrefix, [StringComparison]::OrdinalIgnoreCase) -or
    $buildPath.Equals([IO.Path]::GetPathRoot($buildPath), [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Choose a dedicated build folder outside the source checkout and filesystem root.'
}
$branchName = (& git -C $sourceRoot branch --show-current).Trim()
if ($LASTEXITCODE -ne 0 -or $branchName -ne 'feat/customsforge-song-browser') { throw 'Build from the feat/customsforge-song-browser branch.' }
& git -C $sourceRoot merge-base --is-ancestor 804aa8c91c0cc26809ab8ef97093c0b5d7fa042c HEAD
if ($LASTEXITCODE -ne 0) { throw 'The expected FeedForge baseline is not an ancestor of this build.' }
$revision = (& git -C $sourceRoot rev-parse HEAD).Trim()
$pythonExe = (Resolve-Path -LiteralPath $PythonPath).Path
$electronExe = (Resolve-Path -LiteralPath $ElectronPath).Path
$audioPath = (Resolve-Path -LiteralPath $AudioToolsPath).Path
$nodeExe = (Get-Command node -CommandType Application -ErrorAction Stop).Source
$builderCli = Join-Path $sourceRoot 'node_modules\electron-builder\out\cli\cli.js'
$viteCli = Join-Path $sourceRoot 'node_modules\vite\bin\vite.js'
foreach ($filename in @($builderCli, $viteCli, (Join-Path $audioPath 'vgmstream-cli.exe'), (Join-Path $audioPath 'ffmpeg.exe'))) {
    if (!(Test-Path -LiteralPath $filename -PathType Leaf)) { throw "Missing installed prerequisite: $filename" }
}
$pythonInfo = & $pythonExe -B -c 'import json, sys, importlib.metadata as m; print(json.dumps({"python":sys.version,"packages":{n:m.version(n) for n in ["pyinstaller","construct","cryptography","jsonschema","Pillow","PyYAML","soundfile"]}}))'
if ($LASTEXITCODE -ne 0) { throw 'The selected Python lacks installed converter/PyInstaller dependencies. No dependencies were installed.' }
$electronDist = Split-Path -Parent $electronExe
$electronVersion = (Get-Content -LiteralPath (Join-Path $electronDist 'version') -Raw).Trim()
$receiptPath = Join-Path $buildPath 'song-browser-build.json'
$receipt = $null
if (Test-Path -LiteralPath $buildPath) {
    if (!(Test-Path -LiteralPath $receiptPath -PathType Leaf)) { throw 'Existing build folder has no Song Browser ownership receipt. Choose a new folder.' }
    $receipt = Get-Content -LiteralPath $receiptPath -Raw | ConvertFrom-Json -AsHashtable
    if ($receipt.kind -ne 'feedforge-song-browser-build' -or $receipt.source -ne $sourceRoot -or $receipt.branch -ne $branchName) { throw 'This build belongs to another checkout or feature.' }
    if ($receipt.python -ne $pythonExe -or $receipt.electron -ne $electronExe -or $receipt.audioTools -ne $audioPath -or $receipt.electronVersion -ne $electronVersion) { throw 'Build prerequisites differ from this build receipt. Choose a new build folder.' }
}
if ($Stage -eq 'Package' -and (!$receipt -or $receipt.converter -ne 'complete')) { throw 'Complete the Converter stage in this owned build folder before packaging.' }
if ($Stage -in @('All', 'Converter') -and (($receipt -and $receipt.converter -ne 'pending') -or (Test-Path -LiteralPath (Join-Path $buildPath 'converter-dist')) -or (Test-Path -LiteralPath (Join-Path $buildPath 'converter-work')))) { throw 'Converter output already exists. Preserve this build and choose a new folder for another build.' }
if ($Stage -in @('All', 'Package') -and (($receipt -and $receipt.package -ne 'pending') -or (Test-Path -LiteralPath (Join-Path $buildPath 'app-stage')) -or (Test-Path -LiteralPath (Join-Path $buildPath 'release')))) { throw 'Packaging output already exists. Preserve it and choose a new build folder.' }
[ordered]@{ source = $sourceRoot; revision = $revision; branch = $branchName; build = $buildPath; stage = $Stage; python = ($pythonInfo | ConvertFrom-Json); electron = $electronVersion; mode = $(if ($CheckOnly) { 'checked' } else { 'build' }) } | ConvertTo-Json -Depth 5
if ($CheckOnly) { return }
if (!$receipt) {
    New-Item -ItemType Directory -Path $buildPath | Out-Null
    $receipt = [ordered]@{ kind = 'feedforge-song-browser-build'; source = $sourceRoot; branch = $branchName; revision = $revision; python = $pythonExe; electron = $electronExe; audioTools = $audioPath; electronVersion = $electronVersion; createdAt = [DateTime]::UtcNow.ToString('o'); converter = 'pending'; package = 'pending' }
    $receipt | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $receiptPath -Encoding utf8
    $pythonInfo | Set-Content -LiteralPath (Join-Path $buildPath 'python-dependencies.json') -Encoding utf8
}
function Save-Receipt { $receipt | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $receiptPath -Encoding utf8 }
function Source-ConverterDigest {
    $rows = Get-ChildItem -LiteralPath (Join-Path $sourceRoot 'src\feedback_converter') -Recurse -File |
        Where-Object { $_.Extension -in @('.py', '.json', '.bin') } |
        Sort-Object FullName | ForEach-Object { ($_.FullName.Substring($sourceRoot.Length + 1).Replace('\', '/')) + ' ' + (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash }
    $bytes = [Text.Encoding]::UTF8.GetBytes(($rows -join "`n"))
    return [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($bytes))
}
$environmentBefore = @{}
foreach ($name in @('FEEDFORGE_PACKAGE_SOURCE', 'FEEDFORGE_PACKAGE_AUDIO_TOOLS', 'FEEDFORGE_PACKAGE_ROOT', 'FEEDFORGE_PACKAGE_ELECTRON_DIST', 'PYTHONPATH', 'PYTHONDONTWRITEBYTECODE', 'PYINSTALLER_CONFIG_DIR', 'TEMP', 'TMP', 'TMPDIR', 'CSC_IDENTITY_AUTO_DISCOVERY', 'ELECTRON_SKIP_BINARY_DOWNLOAD')) {
    $environmentBefore[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
Push-Location -LiteralPath $sourceRoot
try {
    $env:FEEDFORGE_PACKAGE_SOURCE = $sourceRoot
    $env:FEEDFORGE_PACKAGE_AUDIO_TOOLS = $audioPath
    $env:FEEDFORGE_PACKAGE_ROOT = $buildPath
    $env:FEEDFORGE_PACKAGE_ELECTRON_DIST = $electronDist
    $env:PYTHONPATH = Join-Path $sourceRoot 'src'
    $env:PYTHONDONTWRITEBYTECODE = '1'
    $env:PYINSTALLER_CONFIG_DIR = Join-Path $buildPath 'pyinstaller-cache'
    $env:CSC_IDENTITY_AUTO_DISCOVERY = 'false'
    $env:ELECTRON_SKIP_BINARY_DOWNLOAD = '1'
    $tempPath = Join-Path $buildPath 'temp'
    if (!(Test-Path -LiteralPath $tempPath)) { New-Item -ItemType Directory -Path $tempPath | Out-Null }
    $env:TEMP = $tempPath; $env:TMP = $tempPath; $env:TMPDIR = $tempPath
    if ($Stage -in @('All', 'Converter')) {
        if ($receipt.converter -ne 'pending' -or (Test-Path -LiteralPath (Join-Path $buildPath 'converter-dist')) -or (Test-Path -LiteralPath (Join-Path $buildPath 'converter-work'))) { throw 'Converter output already exists. Preserve this build and choose a new folder for another build.' }
        $receipt.converter = 'building'; $receipt.converterSourceHash = Source-ConverterDigest; Save-Receipt
        & $pythonExe -B -m PyInstaller --noconfirm --distpath (Join-Path $buildPath 'converter-dist') --workpath (Join-Path $buildPath 'converter-work') (Join-Path $PSScriptRoot 'song-browser-converter.spec')
        if ($LASTEXITCODE -ne 0) { throw 'Standalone converter build failed; its owned output is preserved for inspection.' }
        if ((Source-ConverterDigest) -ne $receipt.converterSourceHash) { throw 'Converter source changed while freezing. Choose a new build folder.' }
        $converterExe = Join-Path $buildPath 'converter-dist\psarc2feedpak\psarc2feedpak.exe'
        & $converterExe --version
        if ($LASTEXITCODE -ne 0) { throw 'The frozen converter did not start.' }
        $receipt.converter = 'complete'; $receipt.converterHash = (Get-FileHash -LiteralPath $converterExe -Algorithm SHA256).Hash; Save-Receipt
    }
    if ($Stage -in @('All', 'Package')) {
        if ((Source-ConverterDigest) -ne $receipt.converterSourceHash) { throw 'Converter source changed since freezing. Rebuild in a new folder.' }
        $stagePath = Join-Path $buildPath 'app-stage'
        if ($receipt.package -ne 'pending' -or (Test-Path -LiteralPath $stagePath) -or (Test-Path -LiteralPath (Join-Path $buildPath 'release'))) { throw 'Packaging output already exists. Preserve it and choose a new build folder.' }
        $mainSource = Get-Content -LiteralPath (Join-Path $sourceRoot 'electron\main.cjs') -Raw
        if ($mainSource -notmatch 'songBrowserTest') { throw 'The isolated packaged-test startup hook has not been integrated yet.' }
        $receipt.package = 'building'; $receipt.packageRevision = (& git -C $sourceRoot rev-parse HEAD).Trim(); Save-Receipt
        New-Item -ItemType Directory -Path $stagePath | Out-Null
        Copy-Item -LiteralPath (Join-Path $sourceRoot 'electron') -Destination $stagePath -Recurse
        if (Test-Path -LiteralPath (Join-Path $sourceRoot 'LICENSE')) { Copy-Item -LiteralPath (Join-Path $sourceRoot 'LICENSE') -Destination $stagePath }
        $manifest = Get-Content -LiteralPath (Join-Path $sourceRoot 'package.json') -Raw | ConvertFrom-Json -AsHashtable
        foreach ($key in @('build', 'scripts', 'dependencies', 'devDependencies')) { $manifest.Remove($key) | Out-Null }
        $manifest.name = 'feedforge-song-browser-test'; $manifest.songBrowserTest = $true
        $manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $stagePath 'package.json') -Encoding utf8
        & $nodeExe $viteCli build --outDir (Join-Path $stagePath 'desktop-dist') --emptyOutDir
        if ($LASTEXITCODE -ne 0) { throw 'The production UI build failed.' }
        & $nodeExe $builderCli --win --x64 --dir --publish never --config (Join-Path $PSScriptRoot 'song-browser-build.config.cjs')
        if ($LASTEXITCODE -ne 0) { throw 'The standalone desktop packaging failed.' }
        $portableFolder = Join-Path $buildPath 'release\win-unpacked'
        $appExe = Join-Path $portableFolder 'FeedForge Song Browser Test.exe'
        if (!(Test-Path -LiteralPath $appExe -PathType Leaf)) { throw 'The expected distinct test executable was not produced.' }
        @('FeedForge Song Browser Test', '', 'Extract the whole ZIP into a writable folder, then double-click FeedForge Song Browser Test.exe.', 'Keep the complete folder together; resources contain the standalone converter and audio tools.', 'This test app has a separate profile and does not install or update normal FeedForge.', 'Find songs opens automatically. Sign in through the app browser when ready.', 'Live CustomsForge / host testing and FeedBack playback verification remain user-assisted checks.') |
            Set-Content -LiteralPath (Join-Path $portableFolder 'START-HERE.txt') -Encoding utf8
        $zipPath = Join-Path $buildPath ('FeedForge-Song-Browser-Test-' + $manifest.version + '-win-x64.zip')
        Compress-Archive -LiteralPath $portableFolder -DestinationPath $zipPath -CompressionLevel Optimal
        $receipt.package = 'complete'; $receipt.portableExe = $appExe; $receipt.zip = $zipPath; $receipt.zipSha256 = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash; $receipt.completedAt = [DateTime]::UtcNow.ToString('o'); Save-Receipt
        [ordered]@{ portableExecutable = $appExe; zip = $zipPath; sha256 = $receipt.zipSha256 } | ConvertTo-Json
    }
} finally {
    Pop-Location
    foreach ($name in $environmentBefore.Keys) { [Environment]::SetEnvironmentVariable($name, $environmentBefore[$name], 'Process') }
}
