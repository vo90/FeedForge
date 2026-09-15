from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from feedback_converter import converter
from feedback_converter.feedpak_validator import (
    FeedpakValidationIssue,
    FeedpakValidationResult,
)
from feedback_converter.validation_pipeline import PathValidationResult


def _run_fixture(tmp_path, monkeypatch, *, total, workers, convert_one, separate_stems=False):
    source = tmp_path / "disc.psarc"
    source.write_bytes(b"fixture")
    entries = [(f"song-{index}", {"index": index}) for index in range(total)]
    targets = [tmp_path / f"song-{index}.feedpak" for index in range(total)]
    progress = []

    monkeypatch.setattr(
        converter,
        "_read_psarc_song_entries",
        lambda *_args, **_kwargs: entries,
    )
    monkeypatch.setattr(converter, "_validate_song_audio_entries", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(converter, "_targets_for_conversion", lambda *_args, **_kwargs: targets)
    monkeypatch.setattr(converter, "convert_psarc", convert_one)

    class PassingValidationPipeline:
        def __init__(self, **_kwargs):
            self.paths = []

        def submit(self, package):
            self.paths.append(str(package))
            return len(self.paths) - 1

        def finish(self):
            return tuple(PathValidationResult(path=path, ok=True) for path in self.paths)

        def close(self, **_kwargs):
            return None

    def finalize(staged, validation, **_kwargs):
        staged.validation = validation
        return staged

    monkeypatch.setattr(converter, "FeedpakValidationPipeline", PassingValidationPipeline)
    monkeypatch.setattr(converter, "_finalize_staged_conversion", finalize)

    result = converter.convert_psarc_songs(
        source,
        workers=workers,
        separate_stems=separate_stems,
        progress_callback=progress.append,
    )
    return result, progress, targets


def _result(target: Path) -> converter.ConversionResult:
    return converter.ConversionResult(
        output_path=target,
        package_dir=target.with_suffix(target.suffix + ".work"),
        manifest={},
        validation=FeedpakValidationResult(ok=True),
    )


def test_multi_song_workers_run_concurrently_but_return_plan_order(tmp_path, monkeypatch):
    active = 0
    maximum_active = 0
    lock = threading.Lock()

    def convert_one(_source, target, **_kwargs):
        nonlocal active, maximum_active
        index = int(Path(target).stem.split("-")[-1])
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        try:
            time.sleep((8 - index) * 0.003)
            return _result(Path(target))
        finally:
            with lock:
                active -= 1

    results, progress, targets = _run_fixture(
        tmp_path,
        monkeypatch,
        total=8,
        workers=4,
        convert_one=convert_one,
    )

    assert 1 < maximum_active <= 4
    assert [item.output_path for item in results] == targets
    assert progress[0] == {
        "stage": "converting",
        "completed": 0,
        "failed": 0,
        "total": 8,
        "workers": 4,
    }
    assert progress[-1]["stage"] == "complete"
    assert progress[-1]["completed"] == 8
    assert progress[-1]["failed"] == 0


def test_multi_song_failure_reports_successes_and_failed_target(tmp_path, monkeypatch):
    def convert_one(_source, target, **_kwargs):
        target = Path(target)
        if target.stem == "song-2":
            raise ValueError("bad chart")
        return _result(target)

    with pytest.raises(converter.MultiSongConversionError) as raised:
        _run_fixture(
            tmp_path,
            monkeypatch,
            total=5,
            workers=3,
            convert_one=convert_one,
        )

    assert [item.output_path.name for item in raised.value.results] == [
        "song-0.feedpak",
        "song-1.feedpak",
        "song-3.feedpak",
        "song-4.feedpak",
    ]
    assert [(item.key, item.output_path.name, item.error) for item in raised.value.failures] == [
        ("song-2", "song-2.feedpak", "bad chart")
    ]


def test_stem_separation_forces_serial_song_processing(tmp_path, monkeypatch):
    active = 0
    maximum_active = 0

    def convert_one(_source, target, **_kwargs):
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        time.sleep(0.002)
        active -= 1
        return _result(Path(target))

    _results, progress, _targets = _run_fixture(
        tmp_path,
        monkeypatch,
        total=4,
        workers=12,
        separate_stems=True,
        convert_one=convert_one,
    )

    assert maximum_active == 1
    assert progress[0]["workers"] == 1


def test_chart_findings_report_success_progress_without_user_warning(
    tmp_path,
    monkeypatch,
):
    message = "arrangements/lead.json: notes/0/f: must be between 0 and 24"

    def convert_one(_source, target, **_kwargs):
        return converter.ConversionResult(
            output_path=Path(target),
            package_dir=Path(target).with_suffix(".work"),
            manifest={},
            validation=FeedpakValidationResult(
                ok=False,
                errors=[message],
                issues=[
                    FeedpakValidationIssue(
                        code="chart.fret.out-of-range",
                        message=message,
                        category="chart-semantic",
                        severity="error",
                        blocking=False,
                    )
                ],
            ),
        )

    results, progress, _targets = _run_fixture(
        tmp_path,
        monkeypatch,
        total=1,
        workers=1,
        convert_one=convert_one,
    )

    assert results[0].converted_with_chart_warnings
    assert results[0].warnings == []
    assert [event["status"] for event in progress if "status" in event] == [
        "succeeded"
    ]


def test_material_warning_remains_visible_in_progress(tmp_path, monkeypatch):
    def convert_one(_source, target, **_kwargs):
        result = _result(Path(target))
        result.warnings.append(converter.ConversionWarning("Skipped playable arrangement."))
        return result

    _results, progress, _targets = _run_fixture(
        tmp_path,
        monkeypatch,
        total=1,
        workers=1,
        convert_one=convert_one,
    )

    assert [event["status"] for event in progress if "status" in event] == [
        "succeeded_with_warnings"
    ]


@pytest.mark.parametrize(
    ("requested", "total", "expected"),
    [(None, 10, 1), (0, 10, 1), (6, 3, 3), (99, 100, 32), ("bad", 10, 1)],
)
def test_song_worker_count_is_bounded(requested, total, expected):
    assert converter._conversion_song_worker_count(
        requested,
        total=total,
        separate_stems=False,
    ) == expected


def test_multi_song_conversion_defaults_to_safe_validation_policy(tmp_path, monkeypatch):
    observed = []

    def convert_one(_source, target, **kwargs):
        observed.append(kwargs["validation_policy"])
        return _result(Path(target))

    _run_fixture(
        tmp_path,
        monkeypatch,
        total=3,
        workers=1,
        convert_one=convert_one,
    )

    assert observed == ["safe", "safe", "safe"]


@pytest.mark.parametrize("value", ["", None, "SAFE", " safe "])
def test_validation_policy_normalizes_safe_default(value):
    assert converter.normalize_validation_policy(value) == "safe"


def test_validation_policy_rejects_unknown_value():
    with pytest.raises(ValueError, match="validation policy"):
        converter.normalize_validation_policy("skip-everything")
