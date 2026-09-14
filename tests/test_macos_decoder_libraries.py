"""The native dependency repair must fail before touching an incompatible binary."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


spec = importlib.util.spec_from_file_location(
    "prepare_macos_decoder", Path(__file__).parents[1] / "tools" / "prepare_macos_decoder.py",
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def native_dependencies(names):
    return "vgmstream-cli:\n" + "".join(
        f"\t/opt/homebrew/opt/ffmpeg/lib/{name} (compatibility version 1.0.0)\n"
        for name in names
    ) + "\t/opt/homebrew/opt/libogg/lib/libogg.0.dylib (compatibility version 1.0.0)\n"


def runner(names):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(stdout=native_dependencies(names))

    return run, calls


def libraries_at(prefix, names):
    (prefix / "lib").mkdir()
    for name in names:
        (prefix / "lib" / name).touch()


def test_rebinds_only_ffmpeg_libraries_and_restores_signature(tmp_path):
    libraries_at(tmp_path, module.FFMPEG_LIBRARIES)
    run, calls = runner(sorted(module.FFMPEG_LIBRARIES))
    decoder = tmp_path / "vgmstream-cli"
    module.prepare_decoder(decoder, tmp_path, run=run)
    changes = [call for call in calls if call[0] == "install_name_tool"]
    assert len(changes) == 4
    assert {Path(call[3]).name for call in changes} == module.FFMPEG_LIBRARIES
    assert all(Path(call[3]).parent == tmp_path / "lib" for call in changes)
    assert not any("libogg" in item for call in changes for item in call)
    assert calls[-1] == ["codesign", "--force", "--sign", "-", str(decoder)]


def test_missing_compatible_library_fails_before_any_binary_mutation(tmp_path):
    libraries_at(tmp_path, module.FFMPEG_LIBRARIES - {"libavcodec.62.dylib"})
    run, calls = runner(sorted(module.FFMPEG_LIBRARIES))
    with pytest.raises(RuntimeError, match="Required FFmpeg 8 library is missing"):
        module.prepare_decoder(tmp_path / "vgmstream-cli", tmp_path, run=run)
    assert len(calls) == 1
    assert calls[0][0] == "otool"


def test_changed_decoder_abi_cannot_silently_use_incompatible_libraries(tmp_path):
    newer_abi = module.FFMPEG_LIBRARIES - {"libavcodec.62.dylib"} | {"libavcodec.63.dylib"}
    libraries_at(tmp_path, newer_abi)
    run, calls = runner(sorted(newer_abi))
    with pytest.raises(RuntimeError, match="ABI differs"):
        module.prepare_decoder(tmp_path / "vgmstream-cli", tmp_path, run=run)
    assert len(calls) == 1
    assert calls[0][0] == "otool"
