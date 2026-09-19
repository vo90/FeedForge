# -*- mode: python ; coding: utf-8 -*-

import os
from importlib.metadata import distribution as installed_distribution
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, copy_metadata


root = Path.cwd()
tools = root / 'src' / 'feedback_converter' / 'tools'
decoder_name = 'vgmstream-cli.exe' if os.name == 'nt' else 'vgmstream-cli'
decoder_candidates = [
    tools,
    *sorted((root / 'runtime').glob('vgmstream-*'), reverse=True),
]
decoder_dir = next(
    (candidate for candidate in decoder_candidates if (candidate / decoder_name).is_file()),
    None,
)
if decoder_dir is None:
    raise SystemExit(
        f'Missing required WEM decoder {decoder_name}. Download the complete vgmstream bundle '
        'into src/feedback_converter/tools or runtime/vgmstream-<version> before packaging.'
    )

native_tools = [(decoder_dir / decoder_name, 'feedback_converter/tools')]
if os.name == 'nt':
    native_tools.extend(
        (item, 'feedback_converter/tools')
        for item in sorted(decoder_dir.glob('*.dll'))
    )
    native_tools.extend(
        (tools / name, 'feedback_converter/tools')
        for name in ('ww2ogg.exe', 'oggenc.exe', 'topng.exe')
        if (tools / name).is_file()
    )
audio_tools = Path(os.environ.get('FEEDFORGE_PACKAGE_AUDIO_TOOLS', str(tools)))
song_binaries, song_datas, song_imports = [], [], []
for name in ('ffmpeg', 'ffprobe', 'node'):
    candidate = audio_tools / (name + ('.exe' if os.name == 'nt' else ''))
    if not candidate.is_file():
        raise RuntimeError(f'Song import packaging requires {candidate}; supply FEEDFORGE_PACKAGE_AUDIO_TOOLS.')
    song_binaries.append((str(candidate), 'feedback_converter/tools'))
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
    binaries=[(str(source), destination) for source, destination in native_tools] + song_binaries,
    datas=[
        (str(tools / 'packed_codebooks.bin'), 'feedback_converter/tools'),
        (
            str(tools / 'packed_codebooks_aoTuV_603.bin'),
            'feedback_converter/tools',
        ),
        (str(root / 'src' / 'feedback_converter' / 'data' / 'equipment.json'), 'feedback_converter/data'),
        (str(root / 'src' / 'feedback_converter' / 'data' / 'feedback_equipment.json'), 'feedback_converter/data'),
        (str(root / 'src' / 'feedback_converter' / 'data' / 'feedpak_schemas'), 'feedback_converter/data/feedpak_schemas'),
    ] + song_datas,
    hiddenimports=song_imports + ['yt_dlp_plugins.extractor.getpot_wpc'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='psarc2feedpak',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='psarc2feedpak',
)
