from __future__ import annotations

from pathlib import Path

import pytest

from feedback_converter import batch, converter
from feedback_converter.batch import _planning_worker_count
from feedback_converter.feedpak_validator import FeedpakValidationResult


@pytest.mark.parametrize("value", [None, "", 0, "invalid"])
def test_planning_worker_count_preserves_default(value: object) -> None:
    assert _planning_worker_count(value, total=100) == 4


@pytest.mark.parametrize(
    ("value", "total", "expected"),
    [
        (-10, 100, 1),
        (1, 100, 1),
        (12, 100, 12),
        (32, 100, 32),
        (48, 100, 32),
        (24, 7, 7),
        (32, 0, 1),
    ],
)
def test_planning_worker_count_clamps_to_supported_range_and_workload(
    value: object,
    total: int,
    expected: int,
) -> None:
    assert _planning_worker_count(value, total=total) == expected


def test_convert_many_retains_successful_songs_from_partial_archive_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "disc.psarc"
    source.write_bytes(b"fixture")
    successful = converter.ConversionResult(
        output_path=tmp_path / "good.feedpak",
        package_dir=tmp_path / "good.work",
        manifest={},
        validation=FeedpakValidationResult(ok=True),
    )
    failure = converter.SongConversionFailure(
        key="bad-song",
        output_path=tmp_path / "bad.feedpak",
        error="bad chart",
    )

    class Plan:
        def to_dict(self):
            return {"outputs": []}

    monkeypatch.setattr(batch, "plan_psarc_songs", lambda *_args, **_kwargs: Plan())

    def partially_fail(*_args, **_kwargs):
        raise converter.MultiSongConversionError([failure], [successful])

    monkeypatch.setattr(batch, "convert_psarc_songs", partially_fail)

    result = batch.convert_many([source])

    assert not result.ok
    assert result.items[0].results == [successful]
    assert result.items[0].song_failures == [failure]
    assert result.items[0].error


def test_convert_many_passes_validation_policy_to_each_psarc(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "song.psarc"
    source.write_bytes(b"fixture")
    successful = converter.ConversionResult(
        output_path=tmp_path / "song.feedpak",
        package_dir=tmp_path / "song.work",
        manifest={},
        validation=FeedpakValidationResult(ok=True),
    )
    observed = []

    class Plan:
        def to_dict(self):
            return {"outputs": []}

    monkeypatch.setattr(batch, "plan_psarc_songs", lambda *_args, **_kwargs: Plan())

    def convert(*_args, **kwargs):
        observed.append(kwargs["validation_policy"])
        return [successful]

    monkeypatch.setattr(batch, "convert_psarc_songs", convert)

    result = batch.convert_many([source], validation_policy="strict")

    assert result.ok
    assert observed == ["strict"]
