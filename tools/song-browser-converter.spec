# -*- mode: python ; coding: utf-8 -*-
"""Standalone test converter; all generated output is supplied by the build script."""
import os
from importlib.metadata import distribution as installed_distribution
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, copy_metadata

root = Path(os.environ['FEEDFORGE_PACKAGE_SOURCE']).resolve(strict=True)
audio_tools = Path(os.environ['FEEDFORGE_PACKAGE_AUDIO_TOOLS']).resolve(strict=True)
tools = root / 'src' / 'feedback_converter' / 'tools'
native_names = ['vgmstream-cli.exe', 'ffmpeg.exe', 'ffprobe.exe', 'node.exe', *[item.name for item in sorted(audio_tools.glob('*.dll'))]]
native_files = []
for name in native_names:
    candidate = audio_tools / name
    if not candidate.is_file():
        raise RuntimeError(f'Missing required audio dependency: {name}')
    native_files.append((str(candidate), 'feedback_converter/tools'))

song_datas, song_binaries, song_imports = [], [], []
for module in ('yt_dlp', 'yt_dlp_ejs', 'imageio_ffmpeg'):
    data, binaries, hidden = collect_all(module)
    song_datas.extend(data); song_binaries.extend(binaries); song_imports.extend(hidden)
for distribution in ('yt-dlp', 'yt-dlp-ejs', 'numpy', 'soundfile', 'imageio-ffmpeg', 'yt-dlp-getpot-wpc'):
    song_datas.extend(copy_metadata(distribution))
# yt-dlp's namespace finder enumerates physical files, not PyInstaller's PYZ.
# Keep the hidden import below for dependencies and also ship the provider source.
provider = installed_distribution('yt-dlp-getpot-wpc')
provider_sources = [item for item in provider.files or ()
                    if item.as_posix().startswith('yt_dlp_plugins/') and item.suffix == '.py']
if not provider_sources:
    raise RuntimeError('The installed yt-dlp audio provider has no discoverable source files.')
song_datas.extend((str(provider.locate_file(item)), str(item.parent)) for item in provider_sources)
song_datas.extend((str(item), 'feedback_converter/tools/licenses') for item in (audio_tools / 'licenses').glob('*') if item.is_file())

a = Analysis(
    [str(root / 'src' / 'feedback_converter' / 'cli.py')],
    pathex=[str(root / 'src')],
    binaries=native_files + song_binaries,
    datas=[
        (str(tools / 'packed_codebooks.bin'), 'feedback_converter/tools'),
        (str(tools / 'packed_codebooks_aoTuV_603.bin'), 'feedback_converter/tools'),
        (str(root / 'src' / 'feedback_converter' / 'data' / 'equipment.json'), 'feedback_converter/data'),
        (str(root / 'src' / 'feedback_converter' / 'data' / 'feedback_equipment.json'), 'feedback_converter/data'),
        (str(root / 'src' / 'feedback_converter' / 'data' / 'feedpak_schemas'), 'feedback_converter/data/feedpak_schemas'),
    ] + song_datas,
    hiddenimports=song_imports + ['yt_dlp_plugins.extractor.getpot_wpc'], hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=[], noarchive=False, optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name='psarc2feedpak',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=True, disable_windowed_traceback=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='psarc2feedpak')
