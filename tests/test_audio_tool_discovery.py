import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from feedback_converter import converter


def _fake_executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake-vgmstream")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path.resolve()


def _isolate_discovery(monkeypatch, tmp_path: Path) -> None:
    module = tmp_path / "repo" / "src" / "feedback_converter" / "converter.py"
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text("", encoding="utf-8")
    monkeypatch.setattr(converter, "__file__", str(module))
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(converter.shutil, "which", lambda _name: None)
    monkeypatch.delenv(converter.FEEDFORGE_TOOLS_DIR_ENV, raising=False)
    monkeypatch.delenv(converter.FEEDFORGE_VGMSTREAM_VERIFIED_ENV, raising=False)


def test_env_override_finds_complete_vgmstream_bundle_first(tmp_path, monkeypatch):
    _isolate_discovery(monkeypatch, tmp_path)
    env_tool = _fake_executable(tmp_path / "configured" / "vgmstream-cli.exe")
    source_tool = _fake_executable(
        tmp_path / "repo" / "src" / "feedback_converter" / "tools" / "vgmstream-cli.exe"
    )
    monkeypatch.setenv(converter.FEEDFORGE_TOOLS_DIR_ENV, str(env_tool.parent))
    monkeypatch.setattr(converter, "_verify_vgmstream_tool", lambda _path: True)

    found = converter._find_vgmstream_tool(source_tool.parent)

    assert found == env_tool


def test_packaged_bundle_is_discovered_under_pyinstaller_root(tmp_path, monkeypatch):
    _isolate_discovery(monkeypatch, tmp_path)
    packaged = _fake_executable(
        tmp_path / "bundle" / "feedback_converter" / "tools" / "vgmstream-cli.exe"
    )
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "bundle"), raising=False)
    monkeypatch.setattr(converter, "_verify_vgmstream_tool", lambda _path: True)

    assert converter._find_vgmstream_tool(tmp_path / "empty") == packaged


def test_desktop_preverified_env_bundle_skips_redundant_probe(tmp_path, monkeypatch):
    _isolate_discovery(monkeypatch, tmp_path)
    decoder = _fake_executable(tmp_path / "preverified" / "vgmstream-cli.exe")
    monkeypatch.setenv(converter.FEEDFORGE_TOOLS_DIR_ENV, str(decoder.parent))
    monkeypatch.setenv(converter.FEEDFORGE_VGMSTREAM_VERIFIED_ENV, "1")
    monkeypatch.setattr(
        converter,
        "_verify_vgmstream_tool",
        lambda _path: (_ for _ in ()).throw(AssertionError("unexpected second probe")),
    )

    assert converter._find_vgmstream_tool(tmp_path / "other-tools") == decoder


def test_source_tools_bundle_is_discovered(tmp_path, monkeypatch):
    _isolate_discovery(monkeypatch, tmp_path)
    source_tool = _fake_executable(
        tmp_path / "repo" / "src" / "feedback_converter" / "tools" / "vgmstream-cli.exe"
    )
    monkeypatch.setattr(converter, "_verify_vgmstream_tool", lambda _path: True)

    assert converter._find_vgmstream_tool(tmp_path / "empty") == source_tool


def test_latest_repo_runtime_bundle_is_discovered(tmp_path, monkeypatch):
    _isolate_discovery(monkeypatch, tmp_path)
    older = _fake_executable(
        tmp_path / "repo" / "runtime" / "vgmstream-r99" / "vgmstream-cli.exe"
    )
    newer = _fake_executable(
        tmp_path / "repo" / "runtime" / "vgmstream-r2117" / "vgmstream-cli.exe"
    )
    monkeypatch.setattr(converter, "_verify_vgmstream_tool", lambda _path: True)

    found = converter._find_vgmstream_tool(tmp_path / "empty")

    assert found == newer
    assert found != older


def test_vgmstream_probe_runs_once_from_executable_directory(tmp_path, monkeypatch):
    decoder = _fake_executable(tmp_path / "complete-bundle" / "vgmstream-cli.exe")
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=1, stdout="vgmstream CLI decoder r2117")

    monkeypatch.setattr(converter.subprocess, "run", run)
    converter._clear_vgmstream_verification_cache()

    assert converter._verify_vgmstream_tool(decoder)
    assert converter._verify_vgmstream_tool(decoder)
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command == [str(decoder), "-h"]
    assert kwargs["cwd"] == str(decoder.parent)
    assert kwargs["timeout"] == converter.VGMSTREAM_VERIFY_TIMEOUT_SECONDS


def test_vgmstream_probe_rejects_executable_that_cannot_start(tmp_path, monkeypatch):
    decoder = _fake_executable(tmp_path / "broken-bundle" / "vgmstream-cli.exe")
    monkeypatch.setattr(
        converter.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("missing companion DLL")),
    )
    converter._clear_vgmstream_verification_cache()

    assert not converter._verify_vgmstream_tool(decoder)


def test_wem_decode_runs_from_located_bundle_for_companion_dlls(tmp_path, monkeypatch):
    decoder = _fake_executable(tmp_path / "complete-bundle" / "vgmstream-cli.exe")
    output = tmp_path / "decoded" / "audio.wav"
    calls = []
    monkeypatch.setattr(converter, "_find_tool", lambda _tools, _name: decoder)

    def run(command, **kwargs):
        calls.append((command, kwargs))
        Path(command[2]).write_bytes(b"R" * 1024)
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(converter.subprocess, "run", run)

    assert converter._decode_wem_with_vgmstream(b"wem", output, tmp_path / "other-tools")
    assert calls[0][1]["cwd"] == str(decoder.parent)
    assert not output.with_name("audio.decode.wem").exists()


def test_wem_conversion_failure_never_publishes_wem_only_stem(tmp_path, monkeypatch):
    package_dir = tmp_path / "package.work"
    (package_dir / "stems").mkdir(parents=True)
    monkeypatch.setattr(converter, "_convert_wem_bytes_to_ogg", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(converter, "_convert_wem_bytes_to_wav", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(converter, "_find_tool", lambda *_args, **_kwargs: None)

    with pytest.raises(converter.WemAudioConversionError, match="without publishing a WEM-only FeedPak"):
        converter._copy_audio(
            {"audio/windows/song.wem": b"wem-data"},
            package_dir,
            [],
        )

    assert not (package_dir / "stems" / "full.wem").exists()


def test_bad_source_wem_is_a_failure_even_when_decoder_exists(tmp_path, monkeypatch):
    package_dir = tmp_path / "package.work"
    (package_dir / "stems").mkdir(parents=True)
    decoder = _fake_executable(tmp_path / "complete-bundle" / "vgmstream-cli.exe")
    monkeypatch.setattr(converter, "_convert_wem_bytes_to_ogg", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(converter, "_convert_wem_bytes_to_wav", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(converter, "_find_tool", lambda *_args, **_kwargs: decoder)

    with pytest.raises(converter.WemAudioConversionError, match="unsupported or damaged"):
        converter._copy_audio(
            {"audio/windows/song.wem": b"bad-wem-data"},
            package_dir,
            [],
        )

    assert not (package_dir / "stems" / "full.wem").exists()
