from pathlib import Path

from feedback_converter.converter import _find_tool


def test_find_tool_prefers_developer_override(tmp_path: Path, monkeypatch) -> None:
    bundled = tmp_path / "bundled"
    override = tmp_path / "override"
    bundled.mkdir()
    override.mkdir()
    bundled_tool = bundled / "vgmstream-cli.exe"
    override_tool = override / "vgmstream-cli.exe"
    bundled_tool.touch()
    override_tool.touch()
    monkeypatch.setenv("FEEDFORGE_NATIVE_TOOLS_DIR", str(override))

    assert _find_tool(bundled, "vgmstream-cli") == override_tool.resolve()


def test_find_tool_falls_back_to_bundled_directory(tmp_path: Path, monkeypatch) -> None:
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    bundled_tool = bundled / "vgmstream-cli.exe"
    bundled_tool.touch()
    monkeypatch.delenv("FEEDFORGE_NATIVE_TOOLS_DIR", raising=False)

    assert _find_tool(bundled, "vgmstream-cli") == bundled_tool.resolve()
