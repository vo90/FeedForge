from __future__ import annotations

import json
from concurrent.futures import Future
from pathlib import Path

import pytest
import yaml

from feedback_converter.feedpak_validator import (
    FeedpakValidationIssue,
    FeedpakValidationResult,
    validate_feedpak,
)
from feedback_converter.validation_pipeline import (
    FeedpakValidationPipeline,
    PathValidationResult,
    ValidationCancelled,
    _decode_worker_payload,
    _validate_path_worker,
    _worker_failure_message,
    _worker_failure_result,
    resolve_validation_worker_count,
    validate_feedpak_paths,
)


def _write_valid_pack(root: Path, name: str) -> Path:
    package = root / name
    (package / "arrangements").mkdir(parents=True)
    (package / "stems").mkdir()
    (package / "arrangements" / "lead.json").write_text(json.dumps({}), encoding="utf-8")
    (package / "stems" / "full.ogg").write_bytes(b"")
    manifest = {
        "feedpak_version": "1.19.0",
        "title": name,
        "artist": "FeedForge",
        "duration": 1.0,
        "arrangements": [{"id": "lead", "file": "arrangements/lead.json"}],
        "stems": [{"id": "full", "file": "stems/full.ogg"}],
    }
    (package / "manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    return package


def test_path_pipeline_preserves_order_and_matches_direct_validation(tmp_path: Path):
    valid_a = _write_valid_pack(tmp_path, "a.feedpak")
    invalid = tmp_path / "missing-manifest.feedpak"
    invalid.mkdir()
    valid_b = _write_valid_pack(tmp_path, "b.feedpak")
    paths = [valid_a, invalid, valid_b, valid_a]

    results = validate_feedpak_paths(paths, workers=99, max_pending=2)

    assert [result.path for result in results] == [str(path.resolve()) for path in paths]
    assert [result.ok for result in results] == [validate_feedpak(path).ok for path in paths]
    assert results[1].errors == ("no manifest.yaml at package root",)
    assert results[1].issues == tuple(validate_feedpak(invalid).issues)
    assert all(result.worker_error is None for result in results)


def test_process_pipeline_preserves_nonblocking_chart_classification(tmp_path: Path):
    package = _write_valid_pack(tmp_path, "odd-chart.feedpak")
    arrangement = package / "arrangements" / "lead.json"
    arrangement.write_text(
        json.dumps({"notes": [{"t": 0.5, "s": 0, "f": 26}]}),
        encoding="utf-8",
    )
    direct = validate_feedpak(package)

    (result,) = validate_feedpak_paths([package], workers=1)

    assert not direct.ok
    assert direct.publishable
    assert result.issues == tuple(direct.issues)
    assert result.as_feedpak_result().publishable
    assert {issue.category for issue in result.issues} == {"chart-semantic"}
    assert all(not issue.blocking for issue in result.issues)


def test_path_result_preserves_structured_issues_when_rebuilding_validation():
    issue = FeedpakValidationIssue(
        code="chart.fret-beyond-highway",
        message="arrangements/lead.json: fret 26 is beyond the highway",
        category="chart-semantic",
        severity="error",
        blocking=False,
    )
    path_result = PathValidationResult(
        path="C:\\staged\\song.feedpak",
        ok=False,
        errors=(issue.message,),
        issues=(issue,),
    )

    rebuilt = path_result.as_feedpak_result()

    assert rebuilt.issues == [issue]
    assert rebuilt.publishable
    assert path_result.to_dict()["issues"] == [issue.to_dict()]


def test_worker_payload_round_trips_structured_issue_data(monkeypatch, tmp_path: Path):
    issue = FeedpakValidationIssue(
        code="timeline.sections-out-of-order",
        message="song_timeline.json: sections are not in chronological order",
        category="chart-semantic",
        severity="error",
        blocking=False,
    )
    expected = FeedpakValidationResult(
        ok=False,
        errors=[issue.message],
        warnings=["kept source order"],
        issues=[issue],
    )
    monkeypatch.setattr(
        "feedback_converter.validation_pipeline.feedpak_validator.validate_feedpak",
        lambda _path: expected,
    )

    payload = _validate_path_worker(str(tmp_path / "song.feedpak"))
    rebuilt, worker_error = _decode_worker_payload(payload)

    assert worker_error is None
    assert rebuilt == expected
    assert rebuilt.publishable


def test_decoder_accepts_legacy_worker_payload():
    rebuilt, worker_error = _decode_worker_payload(
        (False, ("legacy error",), ("legacy warning",), None)
    )

    assert not rebuilt.ok
    assert rebuilt.errors == ["legacy error"]
    assert rebuilt.warnings == ["legacy warning"]
    assert rebuilt.issues == []
    assert not rebuilt.publishable


def test_pipeline_never_exceeds_its_pending_bound(tmp_path: Path):
    paths = [_write_valid_pack(tmp_path, f"{index}.feedpak") for index in range(5)]

    with FeedpakValidationPipeline(workers=2, max_pending=2) as pipeline:
        for path in paths:
            pipeline.submit(path)
            assert pipeline.pending_count <= 2
        results = pipeline.finish()

    assert len(results) == len(paths)
    assert all(result.ok for result in results)


def test_cancellation_before_submission_is_lazy_and_reports_no_results(tmp_path: Path):
    pipeline = FeedpakValidationPipeline(cancel_check=lambda: True)

    with pytest.raises(ValidationCancelled) as caught:
        pipeline.submit(tmp_path / "never-started.feedpak")

    assert caught.value.completed == ()
    assert pipeline.submitted_count == 0
    assert pipeline.pending_count == 0


def test_cancellation_after_submission_joins_the_spawned_worker(tmp_path: Path):
    package = _write_valid_pack(tmp_path, "cancel.feedpak")
    checks = 0

    def cancel_check() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 2

    pipeline = FeedpakValidationPipeline(workers=1, cancel_check=cancel_check)
    pipeline.submit(package)

    with pytest.raises(ValidationCancelled):
        pipeline.submit(package)

    assert pipeline.submitted_count == 1
    assert pipeline.pending_count == 0


def test_worker_failure_is_a_clear_per_path_result(tmp_path: Path):
    error = RuntimeError("worker stopped unexpectedly")
    message = _worker_failure_message(error)

    result = _worker_failure_result(str(tmp_path / "song.feedpak"), message)

    assert not result.ok
    assert result.worker_error == message
    assert result.errors == ("validation worker failed (RuntimeError): worker stopped unexpectedly",)


def test_executor_construction_failure_becomes_a_per_path_result(tmp_path: Path, monkeypatch):
    pipeline = FeedpakValidationPipeline(workers=1)

    def fail_to_start():
        raise OSError("process launch denied")

    monkeypatch.setattr(pipeline, "_ensure_executor", fail_to_start)
    pipeline.submit(tmp_path / "song.feedpak")
    results = pipeline.finish()

    assert len(results) == 1
    assert not results[0].ok
    assert results[0].worker_error == (
        "validation worker failed (OSError): process launch denied"
    )


def test_cancellation_reports_already_finished_pending_results(tmp_path: Path):
    pipeline = FeedpakValidationPipeline(workers=1, cancel_check=lambda: True)
    package = str((tmp_path / "finished.feedpak").resolve())
    future = Future()
    future.set_result((True, (), ("finished warning",), None))
    pipeline._paths.append(package)
    pipeline._pending[future] = 0

    with pytest.raises(ValidationCancelled) as caught:
        pipeline._raise_if_cancelled()

    assert len(caught.value.completed) == 1
    assert caught.value.completed[0].path == package
    assert caught.value.completed[0].ok


@pytest.mark.parametrize(
    ("requested", "expected"),
    [(None, 2), (-1, 1), (0, 1), (1, 1), (2, 2), (8, 2)],
)
def test_validation_worker_count_is_always_one_or_two(requested: int | None, expected: int):
    assert resolve_validation_worker_count(requested) == expected
