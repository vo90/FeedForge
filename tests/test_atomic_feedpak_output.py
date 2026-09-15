from pathlib import Path

import pytest

from feedback_converter import converter
from feedback_converter import feedpak
from feedback_converter.feedpak_validator import (
    FeedpakValidationError,
    FeedpakValidationIssue,
    FeedpakValidationResult,
)


def _validation_result(*, blocking: bool) -> FeedpakValidationResult:
    message = "arrangements/lead.json: notes/0/f: must be between 0 and 24"
    return FeedpakValidationResult(
        ok=False,
        errors=[message],
        issues=[
            FeedpakValidationIssue(
                code=(
                    "package.test-blocker"
                    if blocking
                    else "chart.fret.out-of-range"
                ),
                message=message,
                category="package" if blocking else "chart-semantic",
                severity="error",
                blocking=blocking,
            )
        ],
    )


def _staged_conversion(source: Path, target: Path) -> converter.ConversionResult:
    return converter.ConversionResult(
        output_path=target,
        package_dir=source,
        manifest={"title": "Fixture"},
    )


def test_atomic_zip_commit_preserves_existing_output_when_zip_fails(tmp_path, monkeypatch):
    source = tmp_path / "package.work"
    source.mkdir()
    (source / "manifest.yaml").write_text("feedpak_version: 1.14.0\n", encoding="utf-8")
    target = tmp_path / "song.feedpak"
    target.write_bytes(b"known-good")

    def fail_after_partial(_source: Path, partial: Path) -> None:
        partial.write_bytes(b"incomplete")
        raise OSError("disk full")

    monkeypatch.setattr(converter, "_zip_dir", fail_after_partial)

    with pytest.raises(OSError, match="disk full"):
        converter._commit_zip_dir(source, target)

    assert target.read_bytes() == b"known-good"
    assert not list(tmp_path.glob(".song.feedpak.partial-*"))


def test_atomic_zip_commit_replaces_output_only_after_success(tmp_path):
    source = tmp_path / "package.work"
    source.mkdir()
    (source / "manifest.yaml").write_text("feedpak_version: 1.14.0\n", encoding="utf-8")
    target = tmp_path / "song.feedpak"
    target.write_bytes(b"old")

    converter._commit_zip_dir(source, target)

    assert target.read_bytes().startswith(b"PK")
    assert not list(tmp_path.glob(".song.feedpak.partial-*"))


def test_directory_commit_replaces_output_only_after_staging_is_complete(tmp_path):
    source = tmp_path / ".song.work"
    source.mkdir()
    (source / "manifest.yaml").write_text("title: new\n", encoding="utf-8")
    target = tmp_path / "song.feedpak-dir"
    target.mkdir()
    (target / "manifest.yaml").write_text("title: old\n", encoding="utf-8")

    retained_backup = converter._commit_directory(source, target)

    assert retained_backup is None
    assert not source.exists()
    assert (target / "manifest.yaml").read_text(encoding="utf-8") == "title: new\n"
    assert not list(tmp_path.glob(".song.feedpak-dir.backup-*"))


def test_directory_commit_restores_previous_output_when_publish_fails(tmp_path, monkeypatch):
    source = tmp_path / ".song.work"
    source.mkdir()
    (source / "manifest.yaml").write_text("title: new\n", encoding="utf-8")
    target = tmp_path / "song.feedpak-dir"
    target.mkdir()
    (target / "manifest.yaml").write_text("title: old\n", encoding="utf-8")
    real_replace = converter.os.replace

    def fail_staged_publish(current, destination):
        if Path(current) == source and Path(destination) == target:
            raise OSError("simulated directory commit failure")
        return real_replace(current, destination)

    monkeypatch.setattr(converter.os, "replace", fail_staged_publish)

    with pytest.raises(OSError, match="directory commit failure"):
        converter._commit_directory(source, target)

    assert source.is_dir()
    assert (target / "manifest.yaml").read_text(encoding="utf-8") == "title: old\n"
    assert not list(tmp_path.glob(".song.feedpak-dir.backup-*"))


def test_safe_policy_atomically_publishes_reviewed_chart_warnings(tmp_path):
    source = tmp_path / "package.work"
    source.mkdir()
    (source / "manifest.yaml").write_text("feedpak_version: 1.14.0\n", encoding="utf-8")
    target = tmp_path / "song.feedpak"

    result = converter._finalize_staged_conversion(
        _staged_conversion(source, target),
        _validation_result(blocking=False),
        archive=True,
        keep_workdir=False,
        validation_policy="safe",
    )

    assert target.read_bytes().startswith(b"PK")
    assert not source.exists()
    assert result.converted_with_chart_warnings
    assert result.chart_warning_count == 1
    assert result.warnings == []
    assert any(
        detail.category == "chart-validation"
        and "data preserved" in detail.message
        for detail in result.conversion_details
    )


def test_safe_policy_publishes_unpacked_directory_from_private_staging(tmp_path):
    source = tmp_path / ".package.work"
    source.mkdir()
    (source / "manifest.yaml").write_text("feedpak_version: 1.14.0\n", encoding="utf-8")
    target = tmp_path / "song.feedpak-dir"

    result = converter._finalize_staged_conversion(
        _staged_conversion(source, target),
        _validation_result(blocking=False),
        archive=False,
        keep_workdir=False,
        validation_policy="safe",
    )

    assert result.output_path == target
    assert result.package_dir == target
    assert target.is_dir()
    assert not source.exists()
    assert result.converted_with_chart_warnings


def test_strict_policy_rejects_chart_warning_without_replacing_existing_output(tmp_path):
    source = tmp_path / "package.work"
    source.mkdir()
    (source / "manifest.yaml").write_text("feedpak_version: 1.14.0\n", encoding="utf-8")
    target = tmp_path / "song.feedpak"
    target.write_bytes(b"known-good")

    with pytest.raises(FeedpakValidationError):
        converter._finalize_staged_conversion(
            _staged_conversion(source, target),
            _validation_result(blocking=False),
            archive=True,
            keep_workdir=False,
            validation_policy="strict",
        )

    assert target.read_bytes() == b"known-good"
    assert not source.exists()


def test_safe_policy_still_rejects_blocking_validation_failure(tmp_path):
    source = tmp_path / "package.work"
    source.mkdir()
    (source / "manifest.yaml").write_text("feedpak_version: 1.14.0\n", encoding="utf-8")
    target = tmp_path / "song.feedpak"
    target.write_bytes(b"known-good")

    with pytest.raises(FeedpakValidationError):
        converter._finalize_staged_conversion(
            _staged_conversion(source, target),
            _validation_result(blocking=True),
            archive=True,
            keep_workdir=False,
            validation_policy="safe",
        )

    assert target.read_bytes() == b"known-good"
    assert not source.exists()


def test_feedpak_edit_preserves_original_when_zip_write_fails(tmp_path, monkeypatch):
    source = tmp_path / "package.work"
    source.mkdir()
    (source / "manifest.yaml").write_text("title: replacement\n", encoding="utf-8")
    target = tmp_path / "song.feedpak"
    target.write_bytes(b"old-good-feedpak")

    def fail_after_partial(_source: Path, partial: Path) -> None:
        partial.write_bytes(b"truncated")
        raise OSError("simulated interrupted edit")

    monkeypatch.setattr(converter, "_zip_dir", fail_after_partial)

    with pytest.raises(OSError, match="interrupted edit"):
        feedpak._write_package(source, target, overwrite=True)

    assert target.read_bytes() == b"old-good-feedpak"
    assert not list(tmp_path.glob(".song.feedpak.partial-*"))


def test_psarc_audio_export_preserves_existing_non_wem_on_commit_failure(tmp_path, monkeypatch):
    target = tmp_path / "song.ogg"
    target.write_bytes(b"old-audio")

    monkeypatch.setattr(
        converter.os,
        "replace",
        lambda *_args: (_ for _ in ()).throw(OSError("commit denied")),
    )

    with pytest.raises(OSError, match="commit denied"):
        converter._export_audio_from_content(
            {"audio/song.ogg": b"new-audio"},
            target,
            [],
            overwrite=True,
        )

    assert target.read_bytes() == b"old-audio"
    assert not list(tmp_path.glob(".song.ogg.partial-*"))


def test_psarc_wem_export_preserves_existing_audio_on_commit_failure(tmp_path, monkeypatch):
    target = tmp_path / "song.ogg"
    target.write_bytes(b"old-audio")

    def decode(_data, output, **_kwargs):
        output.write_bytes(b"OggS-new-audio")
        return True

    monkeypatch.setattr(converter, "_convert_wem_bytes_to_ogg", decode)
    monkeypatch.setattr(
        converter.os,
        "replace",
        lambda *_args: (_ for _ in ()).throw(OSError("commit denied")),
    )

    with pytest.raises(OSError, match="commit denied"):
        converter._export_audio_from_content(
            {"audio/song.wem": b"wem"},
            target,
            [],
            overwrite=True,
        )

    assert target.read_bytes() == b"old-audio"
    assert not list(tmp_path.glob(".song.ogg.work-*"))
