"""Contracts that cross the formerly independent v2 and fidelity pipelines."""
from copy import deepcopy
import json
from pathlib import Path
import zipfile

import pytest

from feedback_converter import feedpak, package_io, songsterr_cli
from feedback_converter.feedpak_validator import FeedpakValidationError
from feedback_converter.songsterr import write_feedpak


def _chart():
    return {"name": "Lead", "role": "lead", "tuning": [0] * 6, "capo": 0,
            "notes": [{"t": 1 + index * 0.5, "s": 0, "f": 3, "sus": 0.2}
                      for index in range(8)],
            "chords": [], "templates": [], "anchors": [], "handshapes": [],
            "stats": {"events": 8, "notes": 8}}


def _timeline():
    return {"version": 1, "duration": 6, "beats": [], "sections": [],
            "tempos": [], "time_signatures": []}


def test_editor_directory_copy_failure_preserves_existing_output(tmp_path, monkeypatch):
    source, target = tmp_path / "source", tmp_path / "song-directory"
    source.mkdir()
    target.mkdir()
    (source / "manifest.yaml").write_text("title: new")
    (target / "manifest.yaml").write_text("title: old")

    def partial_copy(source, destination, **_):
        (destination / "partial").write_text("unfinished")
        raise OSError("copy interrupted")

    monkeypatch.setattr(feedpak.shutil, "copytree", partial_copy)
    with pytest.raises(OSError, match="copy interrupted"):
        feedpak._write_package(source, target, overwrite=True)
    assert (target / "manifest.yaml").read_text() == "title: old"
    assert not list(tmp_path.glob(".song-directory.*"))


def test_editor_directory_commit_failure_restores_existing_output(tmp_path, monkeypatch):
    source, target = tmp_path / "source", tmp_path / "song-directory"
    source.mkdir()
    target.mkdir()
    (source / "manifest.yaml").write_text("title: new")
    (target / "manifest.yaml").write_text("title: old")
    real_replace = feedpak.os.replace

    def fail_publish(source, destination):
        if Path(source).name.startswith(".song-directory.staged-"):
            raise OSError("publish interrupted")
        return real_replace(source, destination)

    monkeypatch.setattr(feedpak.os, "replace", fail_publish)
    with pytest.raises(OSError, match="publish interrupted"):
        feedpak._write_package(source, target, overwrite=True)
    assert (target / "manifest.yaml").read_text() == "title: old"
    assert not list(tmp_path.glob(".song-directory.*"))


def test_editor_directory_replacement_completes_before_old_output_removed(tmp_path):
    source, target = tmp_path / "source", tmp_path / "song-directory"
    source.mkdir()
    target.mkdir()
    (source / "manifest.yaml").write_text("title: new")
    (target / "old-file").write_text("old")
    assert feedpak._write_package(source, target, overwrite=True) is None
    assert (target / "manifest.yaml").read_text() == "title: new"
    assert not (target / "old-file").exists()
    assert source.is_dir(), "editor working copy remains owned by its context"


def test_archive_target_directory_is_rejected_without_deletion(tmp_path):
    source, target = tmp_path / "source", tmp_path / "song.feedpak"
    source.mkdir()
    target.mkdir()
    (target / "original").write_text("keep")
    with pytest.raises(IsADirectoryError):
        feedpak._write_package(source, target, overwrite=True)
    assert (target / "original").read_text() == "keep"


def test_common_archive_writer_preserves_old_output_after_member_failure(tmp_path, monkeypatch):
    source, target = tmp_path / "source", tmp_path / "song.feedpak"
    source.mkdir()
    (source / "member").write_bytes(b"content")
    target.write_bytes(b"old archive")

    def fail_member(*_args, **_kwargs):
        raise OSError("member interrupted")

    monkeypatch.setattr(package_io.zipfile.ZipFile, "write", fail_member)
    with pytest.raises(OSError, match="member interrupted"):
        package_io.write_archive(source, target)
    assert target.read_bytes() == b"old archive"
    assert not list(tmp_path.glob(".song.feedpak.*"))


def test_common_archive_writer_rejects_linked_member(tmp_path, monkeypatch):
    source, target = tmp_path / "source", tmp_path / "song.feedpak"
    source.mkdir()
    member = source / "linked"
    member.write_text("content")
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == member or original(path))
    with pytest.raises(ValueError, match="outside its staging directory"):
        package_io.write_archive(source, target)
    assert not target.exists()


def test_editor_uses_shared_semantic_validation_before_overwrite(tmp_path):
    chart = _chart()
    chart["notes"][0]["f"] = 25  # Schema permits this; shared semantics do not.
    audio, output = tmp_path / "audio.ogg", tmp_path / "existing.feedpak"
    audio.write_bytes(b"OggS-test")
    output.write_bytes(b"known-good")
    with pytest.raises(FeedpakValidationError, match="between 0 and 24"):
        write_feedpak([chart], _timeline(), audio, output, title="Source", artist="Artist")
    assert output.read_bytes() == b"known-good"
    assert chart["notes"][0]["f"] == 25, "validation must not repair the source"


def test_editor_contract_uses_shared_unicode_naming_and_explicit_difficulty(tmp_path, monkeypatch):
    audio = tmp_path / "audio.ogg"
    audio.write_bytes(b"OggS-test")
    chart = _chart()
    original = deepcopy(chart)
    inspection = {"meta": {"title": "Räts", "artist": "Ghö st"}}
    monkeypatch.setattr(songsterr_cli, "selected_song", lambda _: (inspection, [1], {}))
    monkeypatch.setattr(songsterr_cli, "prepare_song_audio", lambda *args: (audio, None, ""))
    monkeypatch.setattr(songsterr_cli, "songsterr_to_tracks", lambda _: ([deepcopy(chart)], _timeline()))
    monkeypatch.setattr(songsterr_cli, "prepare_cover", lambda *args: None)
    result = songsterr_cli.create({
        "url": "https://www.songsterr.com/a/wsa/test-tab-s1", "selected_parts": [1],
        "output_path": str(tmp_path / "staged.feedpak"), "album": "Préquelle",
        "outputSettings": {"outputDir": str(tmp_path / "library"), "outputLayout": "artist",
                           "nameTemplate": "{artist} - {title} - {album}", "generateDifficulty": True},
    })
    assert result["relative_path"] == "Ghö st/Ghö st - Räts - Préquelle.feedpak"
    assert not (tmp_path / "library").exists()
    with zipfile.ZipFile(result["output_path"]) as archive:
        wire = json.loads(archive.read("arrangements/lead.json"))
    assert wire["notes"] == original["notes"]
    assert wire["ext"]["generatedDifficulty"]["sourceAuthored"] is False


def test_cli_editor_bridge_does_not_mix_library_stdout_with_result(tmp_path, monkeypatch, capsys):
    from feedback_converter.cli import main
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"action": "analyze", "payload": {}}))

    def noisy_dispatch(_):
        print("third-party diagnostic")
        return {"title": "Song"}

    monkeypatch.setattr(songsterr_cli, "dispatch", noisy_dispatch)
    assert main(["songsterr", str(request)]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"ok": True, "result": {"title": "Song"}}
    assert "third-party diagnostic" in captured.err


def test_editor_health_reports_missing_components_without_inspecting_a_song(monkeypatch):
    def missing(name):
        raise ImportError(f"missing {name}")

    monkeypatch.setattr(songsterr_cli.importlib, "import_module", missing)
    monkeypatch.setattr(songsterr_cli, "inspect_songsterr", lambda *_: pytest.fail("health requested a song"))
    result = songsterr_cli.dispatch({"action": "health"})
    assert result["ok"] is False
    assert result["engine"] == "songsterr-editor"
    assert len(result["dependencies"]) == 4
    assert all(not dependency["available"] for dependency in result["dependencies"].values())
    assert result["ffmpeg"] == {"path": "", "usable": False}


def test_editor_browser_fallback_registers_bundled_provider_without_discovery(monkeypatch):
    from types import SimpleNamespace
    from feedback_converter import songsterr
    from yt_dlp.extractor.youtube.pot._registry import _pot_providers

    monkeypatch.setattr(_pot_providers, "value", {})
    loaded = []

    def import_provider(name):
        loaded.append(name)
        _pot_providers.value["WPC"] = SimpleNamespace(PROVIDER_NAME="wpc")

    monkeypatch.setattr(songsterr.importlib, "import_module", import_provider)
    assert songsterr._load_browser_token_provider() == {"name": "wpc", "registered": True}
    assert songsterr._load_browser_token_provider() == {"name": "wpc", "registered": True}
    assert loaded == ["yt_dlp_plugins.extractor.getpot_wpc"], "registration is idempotent"
