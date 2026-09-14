"""Bind the pinned vgmstream r2117 binary to its FFmpeg 8 ABI before bundling.

Homebrew's unversioned ffmpeg now supplies a newer ABI. PyInstaller follows the
load paths repaired here and copies the compatible libraries into the app.
"""

import argparse
import subprocess
from pathlib import Path


FFMPEG_LIBRARIES = frozenset({
    "libavformat.62.dylib", "libavcodec.62.dylib",
    "libavutil.60.dylib", "libswresample.6.dylib",
})


def prepare_decoder(decoder: Path, ffmpeg_prefix: Path, *, run=subprocess.run):
    dependencies = run(
        ["otool", "-L", str(decoder)], check=True, capture_output=True, text=True,
    ).stdout
    replacements = []
    for line in dependencies.splitlines()[1:]:
        old = line.strip().split(" (", 1)[0]
        if "/opt/ffmpeg/lib/" in old:
            replacements.append((old, ffmpeg_prefix / "lib" / Path(old).name))
    if {new.name for _, new in replacements} != FFMPEG_LIBRARIES:
        raise RuntimeError("Pinned vgmstream FFmpeg ABI differs from the expected FFmpeg 8 libraries.")
    # Validate every dependency before modifying the signed executable.
    for _, new in replacements:
        if not new.is_file():
            raise RuntimeError(f"Required FFmpeg 8 library is missing: {new}")
    for old, new in replacements:
        run(["install_name_tool", "-change", old, str(new), str(decoder)], check=True)
    run(["codesign", "--force", "--sign", "-", str(decoder)], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("decoder", type=Path)
    parser.add_argument("ffmpeg_prefix", type=Path)
    args = parser.parse_args()
    prepare_decoder(args.decoder, args.ffmpeg_prefix)


if __name__ == "__main__":
    main()
