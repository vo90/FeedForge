from __future__ import annotations

import hashlib
import importlib.util
import os
import zipfile
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "tools" / "install-vgmstream.py"
SPEC = importlib.util.spec_from_file_location("feedforge_install_vgmstream", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


def test_checksum_verification_accepts_exact_digest_and_rejects_changes(tmp_path: Path) -> None:
    archive = tmp_path / "decoder.zip"
    archive.write_bytes(b"verified decoder archive")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()

    installer.verify_sha256(archive, digest)
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        installer.verify_sha256(archive, "0" * 64)


def test_native_tool_extraction_uses_safe_basenames_and_expected_files(tmp_path: Path) -> None:
    archive = tmp_path / "decoder.zip"
    executable_name = "vgmstream-cli.exe" if os.name == "nt" else "vgmstream-cli"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(f"nested/bin/{executable_name}", b"executable")
        bundle.writestr("nested/bin/helper.dll", b"library")
        bundle.writestr("nested/readme.txt", b"ignored")

    destination = tmp_path / "installed"
    destination.mkdir()
    installed = installer.extract_native_tools(
        archive,
        tmp_path / "staging",
        destination,
        executable_name,
    )

    assert (destination / executable_name).read_bytes() == b"executable"
    assert not (destination / "readme.txt").exists()
    assert (destination / "helper.dll").exists() is (os.name == "nt")
    assert all(path.parent == destination for path in installed)


def test_native_tool_extraction_rejects_duplicate_basenames(tmp_path: Path) -> None:
    archive = tmp_path / "decoder.zip"
    executable_name = "vgmstream-cli.exe" if os.name == "nt" else "vgmstream-cli"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(f"first/{executable_name}", b"first")
        bundle.writestr(f"second/{executable_name}", b"second")

    with pytest.raises(RuntimeError, match="duplicate native file"):
        installer.extract_native_tools(
            archive,
            tmp_path / "staging",
            tmp_path / "installed",
            executable_name,
        )
