# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path


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


a = Analysis(
    [str(root / 'src' / 'feedback_converter' / 'cli.py')],
    pathex=[],
    binaries=[(str(source), destination) for source, destination in native_tools],
    datas=[
        (str(tools / 'packed_codebooks.bin'), 'feedback_converter/tools'),
        (
            str(tools / 'packed_codebooks_aoTuV_603.bin'),
            'feedback_converter/tools',
        ),
        (str(root / 'src' / 'feedback_converter' / 'data' / 'equipment.json'), 'feedback_converter/data'),
        (str(root / 'src' / 'feedback_converter' / 'data' / 'feedback_equipment.json'), 'feedback_converter/data'),
        (str(root / 'src' / 'feedback_converter' / 'data' / 'feedpak_schemas'), 'feedback_converter/data/feedpak_schemas'),
    ],
    hiddenimports=[],
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
