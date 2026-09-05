[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$PythonPath,
    [Parameter(Mandatory = $true)][string]$ElectronPath,
    [Parameter(Mandatory = $true)][string]$RuntimeRoot,
    [string]$AudioToolsPath,
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
$sourceRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$runtimePath = [IO.Path]::GetFullPath($RuntimeRoot)
$sourcePrefix = $sourceRoot.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
if ($runtimePath.Equals($sourceRoot, [StringComparison]::OrdinalIgnoreCase) -or
    $runtimePath.StartsWith($sourcePrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Choose a runtime folder outside the source checkout.'
}
$branchName = (& git -C $sourceRoot branch --show-current).Trim()
if ($LASTEXITCODE -ne 0 -or $branchName -ne 'feat/customsforge-song-browser') {
    throw 'This launcher requires the feat/customsforge-song-browser branch.'
}
& git -C $sourceRoot merge-base --is-ancestor 804aa8c91c0cc26809ab8ef97093c0b5d7fa042c HEAD
if ($LASTEXITCODE -ne 0) { throw 'The expected FeedForge baseline is not an ancestor of this build.' }
$pythonExe = (Resolve-Path -LiteralPath $PythonPath).Path
$electronExe = (Resolve-Path -LiteralPath $ElectronPath).Path
if (!(Test-Path -LiteralPath (Join-Path $sourceRoot 'desktop-dist\index.html') -PathType Leaf)) {
    throw 'Build the UI with npm run build before starting this development version.'
}
if ($AudioToolsPath) {
    $audioPath = (Resolve-Path -LiteralPath $AudioToolsPath).Path
    foreach ($toolName in @('vgmstream-cli.exe', 'ffmpeg.exe')) {
        if (!(Test-Path -LiteralPath (Join-Path $audioPath $toolName) -PathType Leaf)) { throw "Missing audio tool: $toolName" }
    }
}
$receiptPath = Join-Path $runtimePath 'song-browser-runtime.json'
if (Test-Path -LiteralPath $runtimePath) {
    if (!(Test-Path -LiteralPath $receiptPath -PathType Leaf)) { throw 'Existing runtime folder has no Song Browser ownership receipt. Choose a new folder.' }
    $receipt = Get-Content -LiteralPath $receiptPath -Raw | ConvertFrom-Json
    if ($receipt.source -ne $sourceRoot -or $receipt.branch -ne $branchName) { throw 'This runtime belongs to another checkout.' }
}
$previousPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = Join-Path $sourceRoot 'src'
    & $pythonExe -B -m feedback_converter.cli --version
    if ($LASTEXITCODE -ne 0) { throw 'The selected Python cannot run this checkout of FeedForge.' }
} finally { $env:PYTHONPATH = $previousPythonPath }
$plan = [ordered]@{ source = $sourceRoot; branch = $branchName; runtime = $runtimePath; python = $pythonExe; electron = $electronExe; audioTools = $AudioToolsPath; mode = $(if ($CheckOnly) { 'checked' } else { 'launch' }) }
$plan | ConvertTo-Json
if ($CheckOnly) { return }
if (!(Test-Path -LiteralPath $runtimePath)) {
    New-Item -ItemType Directory -Path $runtimePath | Out-Null
    [ordered]@{ source = $sourceRoot; branch = $branchName; createdAt = [DateTime]::UtcNow.ToString('o') } |
        ConvertTo-Json | Set-Content -LiteralPath $receiptPath -Encoding utf8
}
$environmentBefore = @{}
foreach ($name in @('FEEDFORGE_USER_DATA', 'FEEDFORGE_PYTHON', 'FEEDFORGE_START_VIEW', 'PATH', 'ELECTRON_RUN_AS_NODE')) {
    $environmentBefore[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
try {
    $env:FEEDFORGE_USER_DATA = Join-Path $runtimePath 'profile'
    $env:FEEDFORGE_PYTHON = $pythonExe
    $env:FEEDFORGE_START_VIEW = 'songs'
    $env:ELECTRON_RUN_AS_NODE = $null
    if ($audioPath) { $env:PATH = $audioPath + [IO.Path]::PathSeparator + $env:PATH }
    Start-Process -FilePath $electronExe -ArgumentList @('"' + $sourceRoot + '"') -WorkingDirectory $sourceRoot -WindowStyle Hidden |
        Out-Null
} finally {
    foreach ($name in $environmentBefore.Keys) { [Environment]::SetEnvironmentVariable($name, $environmentBefore[$name], 'Process') }
}
