from __future__ import annotations

import runpy
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace


def test_portable_spec_bundles_the_selected_decoder_dlls(tmp_path: Path, monkeypatch) -> None:
    """Inspect specification inputs without running PyInstaller or any binaries."""
    source = tmp_path / "source"
    old_tools = source / "src" / "feedback_converter" / "tools"
    old_tools.mkdir(parents=True)
    (old_tools / "obsolete-decoder.dll").write_bytes(b"source version")
    selected_tools = tmp_path / "selected-tools"
    selected_tools.mkdir()
    native_names = ["vgmstream-cli.exe", "ffmpeg.exe", "ffprobe.exe", "node.exe", "current-decoder.dll"]
    for name in native_names:
        (selected_tools / name).write_bytes(b"fixture; never executed")
    monkeypatch.setenv("FEEDFORGE_PACKAGE_SOURCE", str(source))
    monkeypatch.setenv("FEEDFORGE_PACKAGE_AUDIO_TOOLS", str(selected_tools))

    for name in ["PyInstaller", "PyInstaller.utils", "PyInstaller.utils.hooks"]:
        monkeypatch.setitem(sys.modules, name, ModuleType(name))
    hooks = sys.modules["PyInstaller.utils.hooks"]
    hooks.collect_all = lambda _module: ([], [], [])
    hooks.copy_metadata = lambda _distribution: []
    analysis = {}

    def inspect_analysis(*args, **kwargs):
        analysis.update(kwargs)
        return SimpleNamespace(pure=[], scripts=[], binaries=[], datas=[])

    specification = Path(__file__).resolve().parents[1] / "tools" / "song-browser-converter.spec"
    runpy.run_path(str(specification), init_globals={
        "Analysis": inspect_analysis,
        "PYZ": lambda *args, **kwargs: None,
        "EXE": lambda *args, **kwargs: None,
        "COLLECT": lambda *args, **kwargs: None,
    })

    assert {Path(filename).name for filename, _destination in analysis["binaries"]} == set(native_names)
    assert all(Path(filename).parent == selected_tools for filename, _destination in analysis["binaries"])
    assert analysis["pathex"] == [str(source / "src")]
