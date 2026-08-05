"""Install FeedForge's pinned native WEM decoder for local and release builds."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path


RELEASE = "r2117"
RELEASE_BASE_URL = f"https://github.com/vgmstream/vgmstream/releases/download/{RELEASE}"
PLATFORMS = {
    "win32": {
        "archive": "vgmstream-win.zip",
        "sha256": "4fa8f0f567a3636e45931b8462a04898fbceca17290a1458b80063b9558b323b",
        "executable": "vgmstream-cli.exe",
    },
    "darwin": {
        "archive": "vgmstream-mac.zip",
        "sha256": "cdabf633231a4556feb0048a71990c768e862f3695dd9458ae28e4d94aa50a99",
        "executable": "vgmstream-cli",
    },
    "linux": {
        "archive": "vgmstream-linux.zip",
        "sha256": "2f98c77f756079f63fbd119939067f1ed461d77e70993bc4cc372736d859c84a",
        "executable": "vgmstream-cli",
    },
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check availability without downloading.")
    parser.add_argument("--force", action="store_true", help="Download and reinstall even if the decoder works.")
    parser.add_argument(
        "--destination",
        type=Path,
        default=Path(__file__).resolve().parents[1] / ".feedforge-tools" / "vgmstream",
        help="Destination directory for the native decoder files.",
    )
    args = parser.parse_args()

    platform_config = PLATFORMS.get(sys.platform)
    if platform_config is None:
        print(f"Unsupported platform for the bundled decoder: {sys.platform}", file=sys.stderr)
        return 2

    destination = args.destination.resolve()
    executable = destination / platform_config["executable"]
    if decoder_works(executable):
        print(f"vgmstream decoder ready: {executable}")
        if not args.force:
            return 0
    elif args.check:
        print(f"vgmstream decoder is missing or unusable: {executable}", file=sys.stderr)
        return 1

    if args.check:
        return 0

    destination.mkdir(parents=True, exist_ok=True)
    archive_name = platform_config["archive"]
    url = f"{RELEASE_BASE_URL}/{archive_name}"
    with tempfile.TemporaryDirectory(prefix="feedforge-vgmstream-") as temporary:
        temporary_dir = Path(temporary)
        archive = temporary_dir / archive_name
        download(url, archive)
        verify_sha256(archive, platform_config["sha256"])
        installed = extract_native_tools(
            archive,
            temporary_dir / "staging",
            destination,
            platform_config["executable"],
        )

    if os.name != "nt":
        executable.chmod(executable.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    if not decoder_works(executable):
        raise RuntimeError(f"Installed decoder did not pass its smoke test: {executable}")
    print(f"Installed verified vgmstream {RELEASE} decoder ({len(installed)} files): {executable}")
    return 0


def download(url: str, destination: Path) -> None:
    print(f"Downloading {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "FeedForge decoder installer"})
    with urllib.request.urlopen(request, timeout=120) as response, destination.open("wb") as target:
        if response.status != 200:
            raise RuntimeError(f"Decoder download failed with HTTP {response.status}")
        shutil.copyfileobj(response, target)


def verify_sha256(archive: Path, expected: str) -> None:
    digest = hashlib.sha256()
    with archive.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual != expected:
        raise RuntimeError(f"Decoder checksum mismatch: expected {expected}, received {actual}")


def extract_native_tools(
    archive: Path,
    staging: Path,
    destination: Path,
    executable_name: str,
) -> list[Path]:
    staging.mkdir(parents=True, exist_ok=True)
    wanted_executable = executable_name.casefold()
    extracted: dict[str, Path] = {}
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            name = Path(member.filename).name
            lowered = name.casefold()
            wanted = lowered == wanted_executable or (os.name == "nt" and lowered.endswith(".dll"))
            if not name or member.is_dir() or not wanted:
                continue
            if lowered in extracted:
                raise RuntimeError(f"Decoder archive contains duplicate native file: {name}")
            staged_file = staging / name
            with bundle.open(member) as source, staged_file.open("wb") as target:
                shutil.copyfileobj(source, target)
            extracted[lowered] = staged_file

    if wanted_executable not in extracted:
        raise RuntimeError(f"Decoder archive did not contain {executable_name}")

    installed = []
    for staged_file in extracted.values():
        target = destination / staged_file.name
        os.replace(staged_file, target)
        installed.append(target)
    return installed


def decoder_works(executable: Path) -> bool:
    if not executable.is_file():
        return False
    try:
        result = subprocess.run(
            [str(executable), "-h"],
            cwd=str(executable.parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return "vgmstream" in result.stdout.casefold()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, urllib.error.URLError, zipfile.BadZipFile) as error:
        print(f"Could not install vgmstream: {error}", file=sys.stderr)
        raise SystemExit(1) from error
